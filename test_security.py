import io
import json
from pathlib import Path
import secrets
import sqlite3

import pytest
from app import create_app


@pytest.fixture
def app(tmp_path):
    return create_app({'APP_ENV': 'testing', 'ADMIN_API_KEY': secrets.token_urlsafe(32),
                       'DATABASE': str(tmp_path / 'test.db'), 'RATELIMIT_ENABLED': False})


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth(app):
    return {'X-Admin-Key': app.config['ADMIN_API_KEY']}


CONTACT = {'name': 'Test Person', 'email': 'person@example.com', 'phone': '+94 771234567',
           'interest': 'canada-pr', 'message': 'Please contact me.'}
REVIEW = {'name': 'Test Person', 'rating': 5, 'body': 'A helpful consultation.'}


@pytest.mark.parametrize('method,path', [
    ('get', '/api/submissions'), ('get', '/api/admin/reviews'),
    ('patch', '/api/admin/reviews/1'), ('post', '/api/admin/reviews/1/publish'),
    ('post', '/api/admin/reviews/1/unpublish'), ('delete', '/api/admin/reviews/1'),
    ('post', '/api/admin/reviews/1/delete'),
])
def test_all_admin_operations_require_auth(client, method, path):
    call = getattr(client, method)
    assert call(path).status_code == 401
    for old in ['123456', '1234', 'Fuckuuu']:
        assert call(path, headers={'X-Admin-Key': old}).status_code == 401


def test_url_and_cookie_auth_rejected(client, auth):
    assert client.get('/api/submissions?key=' + auth['X-Admin-Key']).status_code == 400
    assert client.get('/api/submissions?admin_key=' + auth['X-Admin-Key']).status_code == 400
    client.set_cookie('admin_key', auth['X-Admin-Key'])
    assert client.get('/api/submissions').status_code == 401
    assert client.get('/api/submissions', headers=auth).status_code == 200


def test_contact_roundtrip_and_sql_injection(client, auth):
    payload = dict(CONTACT, name="Robert'); DROP TABLE submissions;--", message='<script>alert(1)</script>')
    assert client.post('/api/contact', json=payload).status_code == 201
    row = client.get('/api/submissions', headers=auth).json['submissions'][0]
    assert all(row[k] == v for k, v in payload.items())


@pytest.mark.parametrize('body', [None, [], True, 123, 'hello', {'name': []}, dict(CONTACT, name=3),
                                 dict(CONTACT, email={}), dict(CONTACT, phone=['12345678']),
                                 dict(CONTACT, interest='admin'), dict(CONTACT, extra=True),
                                 dict(CONTACT, name='a'*101), dict(CONTACT, message='a'*4001),
                                 dict(CONTACT, phone='1'*16), dict(CONTACT, email='x\r\ny@example.com')])
def test_invalid_contact(client, body):
    response = client.post('/api/contact', data=json.dumps(body), content_type='application/json')
    assert response.status_code == 400


@pytest.mark.parametrize('rating', [True, False, '5', 1.5, [], {}, None, 0, 6])
def test_invalid_ratings(client, rating):
    assert client.post('/api/reviews', json=dict(REVIEW, rating=rating)).status_code == 400


def test_json_size_and_upload_rejection(client):
    assert client.post('/api/contact', data='{', content_type='application/json').status_code == 400
    assert client.post('/api/contact', data=CONTACT).status_code == 415
    assert client.post('/api/contact', data={'file': (io.BytesIO(b'data'), 'test.php')}).status_code == 415
    assert client.post('/api/contact', json=dict(CONTACT, message='x'*17000)).status_code == 413


def test_review_moderation_lifecycle(client, auth):
    assert client.post('/api/reviews', json=REVIEW).status_code == 201
    assert client.get('/api/reviews').json['reviews'] == []
    row = client.get('/api/admin/reviews', headers=auth).json['reviews'][0]
    path = '/api/admin/reviews/' + str(row['id'])
    for bad in [{}, {'approved': 'false'}, {'approved': 1}, {'approved': True, 'role': 'admin'}, []]:
        assert client.patch(path, json=bad, headers=auth).status_code == 400
    assert client.patch(path, json={'approved': True}, headers=auth).status_code == 200
    assert len(client.get('/api/reviews').json['reviews']) == 1
    assert client.post(path + '/unpublish', headers=auth).status_code == 200
    assert client.get('/api/reviews').json['reviews'] == []
    assert client.post(path + '/publish', headers=auth).status_code == 200
    assert client.delete(path, headers=auth).status_code == 200
    assert client.delete(path, headers=auth).status_code == 404
    assert client.post('/api/admin/reviews/99999999999999999999999999/publish', headers=auth).status_code == 404


def test_pagination(client, app, auth):
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.executemany('INSERT INTO reviews(name,rating,body,approved,created_at) VALUES(?,?,?,?,?)',
                       [('Test', 5, 'Review body', 1, '2026-09-23')]*105)
    first = client.get('/api/admin/reviews?limit=100', headers=auth).json
    assert len(first['reviews']) == 100
    second = client.get('/api/admin/reviews?limit=100&before=' + str(first['next_cursor']), headers=auth).json
    assert len(second['reviews']) == 5
    assert second['next_cursor'] is None
    assert set(r['id'] for r in first['reviews']).isdisjoint(r['id'] for r in second['reviews'])
    for query in ['limit=101', 'limit=-1', 'limit=x', 'before=0', 'before=9999999999999999999999', 'limit=1&limit=2', 'foo=bar']:
        assert client.get('/api/reviews?' + query).status_code == 400


def test_cors_hosts_and_security_headers(client):
    assert client.get('/api/health', headers={'Host': 'evil.example'}).status_code == 400
    denied = client.post('/api/contact', json=CONTACT, headers={'Origin': 'https://evil.example'})
    assert denied.status_code == 403
    assert 'Access-Control-Allow-Origin' not in denied.headers
    allowed = client.options('/api/contact', headers={'Origin': 'http://localhost:5175',
                                                    'Access-Control-Request-Method': 'POST'})
    assert allowed.headers['Access-Control-Allow-Origin'] == 'http://localhost:5175'
    assert 'Access-Control-Allow-Credentials' not in allowed.headers
    for path in ['/api/health', '/missing', '/api/submissions']:
        response = client.get(path)
        assert response.headers['X-Content-Type-Options'] == 'nosniff'
        assert response.headers['Cache-Control'] == 'no-store'
        assert response.headers['X-Frame-Options'] == 'DENY'
        assert 'Set-Cookie' not in response.headers
        assert response.headers['X-Request-ID']


def test_method_error_retains_allow(client):
    response = client.delete('/api/contact')
    assert response.status_code == 405
    assert 'POST' in response.headers['Allow']
    assert response.is_json


def test_rate_limits_and_forwarded_ip_not_trusted(tmp_path):
    app = create_app({'APP_ENV': 'testing', 'ADMIN_API_KEY': secrets.token_urlsafe(32),
                      'DATABASE': str(tmp_path/'limits.db')})
    client = app.test_client()
    for _ in range(5):
        assert client.post('/api/contact', json=CONTACT).status_code == 201
    response = client.post('/api/contact', json=CONTACT, headers={'X-Forwarded-For': '8.8.8.8'})
    assert response.status_code == 429
    assert int(response.headers['Retry-After']) >= 0
    for _ in range(10):
        assert client.get('/api/submissions').status_code == 401
    assert client.get('/api/submissions').status_code == 429
    assert client.get('/api/submissions', headers={'X-Admin-Key': app.config['ADMIN_API_KEY']}).status_code == 200


def test_no_sensitive_logs(client, caplog, auth):
    caplog.set_level('INFO')
    client.post('/api/contact', json=CONTACT)
    client.get('/api/submissions', headers=auth)
    assert CONTACT['email'] not in caplog.text
    assert CONTACT['phone'] not in caplog.text
    assert auth['X-Admin-Key'] not in caplog.text
    assert 'http_request' in caplog.text


def test_database_failure_is_generic(client, app, caplog):
    app.config['DATABASE'] = str(Path(app.config['DATABASE'])/'missing.db')
    response = client.get('/api/reviews')
    assert response.status_code == 503
    assert 'sqlite' not in response.get_data(as_text=True).lower()
    assert 'Traceback' not in response.get_data(as_text=True)
    assert 'internal_error' in caplog.text


def test_fail_closed_configuration(tmp_path, monkeypatch):
    for key in ['ADMIN_API_KEY', 'ALLOWED_ORIGINS', 'TRUSTED_HOSTS', 'APP_ENV', 'RATELIMIT_STORAGE_URI']:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError):
        create_app()
    base = {'ADMIN_API_KEY': secrets.token_urlsafe(32), 'DATABASE': str(tmp_path/'prod.db')}
    with pytest.raises(RuntimeError, match='TRUSTED_HOSTS'):
        create_app(base)
    base['TRUSTED_HOSTS'] = ['example.com']
    with pytest.raises(RuntimeError, match='ALLOWED_ORIGINS'):
        create_app(base)
    base['ALLOWED_ORIGINS'] = ['https://example.com']
    with pytest.raises(RuntimeError, match='Redis'):
        create_app(base)
    base.update(RATELIMIT_STORAGE_URI='redis://localhost:6379/0', RATELIMIT_ENABLED=False)
    app = create_app(base)
    response = app.test_client().get('/api/health', base_url='https://example.com')
    assert response.status_code == 200
    assert 'max-age=' in response.headers['Strict-Transport-Security']
    assert app.test_client().get('/api/health', base_url='http://example.com').status_code == 400


def test_frontend_no_inline_scripts_or_handlers():
    import re
    public = Path(__file__).parent.parent/'Expert4visas-frontend/public'
    if not public.is_dir():
        pytest.skip('Companion frontend repository is not present')
    html = (public/'admin.html').read_text(encoding='utf-8')
    js = (public/'admin.js').read_text(encoding='utf-8')
    assert not re.search(r'on(?:click|input|change)=', html + js)
    assert '<script>' not in html
    assert 'localStorage' not in js and 'sessionStorage' not in js
    assert '${label}' not in js


def test_existing_database_is_preserved(tmp_path):
    path = tmp_path/'legacy.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, rating INTEGER NOT NULL, body TEXT NOT NULL, approved INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)')
        db.execute("INSERT INTO reviews(name,rating,body,created_at) VALUES('Legacy Reviewer',5,'Existing review','2026-01-01')")
    app = create_app({'APP_ENV':'testing', 'ADMIN_API_KEY':secrets.token_urlsafe(32),
                      'DATABASE':str(path), 'RATELIMIT_ENABLED':False})
    client = app.test_client()
    assert client.get('/api/reviews').json['reviews'][0]['name'] == 'Legacy Reviewer'
    assert client.post('/api/reviews', json=REVIEW).status_code == 201
    assert len(client.get('/api/reviews').json['reviews']) == 1


def test_limit_store_failure_fails_closed(tmp_path, monkeypatch):
    app = create_app({'APP_ENV':'testing', 'ADMIN_API_KEY':secrets.token_urlsafe(32),
                      'DATABASE':str(tmp_path/'failure.db')})
    limiter = app.extensions['security_limiter']
    def unavailable(*args, **kwargs):
        raise ConnectionError('Private storage connection information')
    monkeypatch.setattr(limiter.limiter, 'hit', unavailable)
    response = app.test_client().post('/api/contact', json=CONTACT)
    assert response.status_code == 503
    assert 'Private storage' not in response.get_data(as_text=True)
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT count(*) FROM submissions').fetchone()[0] == 0
