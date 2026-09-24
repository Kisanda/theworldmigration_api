import sqlite3
from test_content import app, client, auth

def test_separate_scope_csrf_logout(client,auth,app):
    assert client.get('/api/admin/reviews').status_code==401
    assert client.post('/api/editor/content/hero/publish',json={'revision':0}).status_code==403
    assert client.post('/api/editor/content/hero/publish',headers={**auth,'Origin':'https://evil.test'},json={'revision':0}).status_code==403
    assert client.post('/api/editor/logout',headers=auth,json={}).status_code==200
    assert client.get('/api/editor/content').status_code==401
    assert client.get('/api/editor/content',headers={'X-Admin-Key':app.config['ADMIN_API_KEY']}).status_code==401

def test_cookie_expiry_rotation(client,auth,app):
    cookie=client.get_cookie('website_editor',path='/api/editor')
    assert cookie.http_only and cookie.same_site=='Strict'
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT token FROM editor_sessions').fetchone()[0] != cookie.value
        db.execute('UPDATE editor_sessions SET expires=0')
    assert client.get('/api/editor/session').status_code==401
    assert client.post('/api/editor/login',json={'password':'TestEditorPassword123'}).status_code==200
    app.config['EDITOR_PASSWORD_HASH']='rotated'
    assert client.get('/api/editor/session').status_code==401

def test_login_fail_closed(client,app):
    assert client.post('/api/editor/login',json={'password':'TestEditorPassword123'}).status_code==503
    from werkzeug.security import generate_password_hash
    app.config['EDITOR_PASSWORD_HASH']=generate_password_hash('TestEditorPassword123')
    assert client.post('/api/editor/login',json={'password':'WrongPassword123'}).status_code==401
    assert client.post('/api/editor/login',json={'password':[]}).status_code==401

def test_production_cookie(client,auth,app):
    app.config['APP_ENV']='production'
    result=client.post('/api/editor/login',json={'password':'TestEditorPassword123'},base_url='https://localhost')
    assert result.status_code==200
    assert 'Secure' in result.headers['Set-Cookie']
