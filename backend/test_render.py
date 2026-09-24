"""Check production proxy handling and API-subdomain editor sessions."""
import secrets

import pytest
from waitress.proxy_headers import proxy_headers_middleware
from werkzeug.security import generate_password_hash

from app import create_app
import serve_render


@pytest.fixture
def render_client(tmp_path, monkeypatch):
    settings = {
        'RENDER': 'true',
        'RENDER_EXTERNAL_HOSTNAME': 'example-service.onrender.com',
        'APP_ENV': 'production',
        'PROXY_HOPS': '0',
        'PORT': '10000',
        'ALLOWED_ORIGINS': 'https://theworldmigration.com,https://www.theworldmigration.com',
        'TRUSTED_HOSTS': 'api.theworldmigration.com',
        'DATABASE_PATH': str(tmp_path / 'render.db'),
        'RATELIMIT_STORAGE_URI': 'redis://127.0.0.1:6379/0',
        'ADMIN_API_KEY': secrets.token_urlsafe(32),
        'EDITOR_PASSWORD_HASH': generate_password_hash('RenderEditorPassword123'),
    }
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    # Exercise production validation without requiring a live Redis server.
    monkeypatch.setattr(serve_render, 'create_app',
                        lambda: create_app({'RATELIMIT_ENABLED': False}))
    app = serve_render.create_render_app()
    options = serve_render.server_options()
    app.wsgi_app = proxy_headers_middleware(
        app.wsgi_app,
        trusted_proxy=options['trusted_proxy'],
        trusted_proxy_count=options['trusted_proxy_count'],
        trusted_proxy_headers=options['trusted_proxy_headers'],
        clear_untrusted=options['clear_untrusted_proxy_headers'],
    )
    return app.test_client()


def test_https_and_hostname_handling(render_client):
    for host in ['api.theworldmigration.com', 'example-service.onrender.com']:
        response = render_client.get('/api/health', base_url='http://' + host,
                                     headers={'X-Forwarded-Proto': 'https'})
        assert response.status_code == 200
        assert response.json == {'status': 'ok'}
    insecure = render_client.get('/api/health', base_url='http://api.theworldmigration.com')
    assert insecure.status_code == 400
    forged = render_client.get('/api/health', base_url='http://evil.example', headers={
        'X-Forwarded-Proto': 'https', 'X-Forwarded-Host': 'api.theworldmigration.com'})
    assert forged.status_code == 400


def test_api_subdomain_editor_session(render_client):
    origin = 'https://theworldmigration.com'
    base = 'https://api.theworldmigration.com'
    preflight = render_client.options('/api/editor/login', base_url=base, headers={
        'Origin': origin, 'Access-Control-Request-Method': 'POST'})
    assert preflight.headers['Access-Control-Allow-Origin'] == origin
    assert preflight.headers['Access-Control-Allow-Credentials'] == 'true'
    login = render_client.post('/api/editor/login', base_url=base,
        headers={'Origin': origin}, json={'password': 'RenderEditorPassword123'})
    assert login.status_code == 200
    assert 'Secure' in login.headers['Set-Cookie']
    assert 'HttpOnly' in login.headers['Set-Cookie']
    assert 'SameSite=Strict' in login.headers['Set-Cookie']
    session = render_client.get('/api/editor/session', base_url=base,
                                headers={'Origin': origin})
    assert session.status_code == 200
    assert session.json['csrf'] == login.json['csrf']
    denied = render_client.post('/api/editor/logout', base_url=base,
                                 headers={'Origin': origin}, json={})
    assert denied.status_code == 403
    logout = render_client.post('/api/editor/logout', base_url=base, headers={
        'Origin': origin, 'X-CSRF-Token': login.json['csrf']}, json={})
    assert logout.status_code == 200
    assert render_client.get('/api/editor/session', base_url=base).status_code == 401


def test_unapproved_editor_origin_is_rejected(render_client):
    response = render_client.options('/api/editor/login',
        base_url='https://api.theworldmigration.com', headers={'Origin': 'https://evil.example'})
    assert response.status_code == 403
    assert 'Access-Control-Allow-Origin' not in response.headers
    assert 'Access-Control-Allow-Credentials' not in response.headers


def test_render_entrypoint_rejects_double_proxy_handling(monkeypatch):
    monkeypatch.setenv('RENDER', 'true')
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('PROXY_HOPS', '1')
    with pytest.raises(RuntimeError, match='PROXY_HOPS=0'):
        serve_render.create_render_app()


def test_render_entrypoint_requires_managed_network(monkeypatch):
    monkeypatch.delenv('RENDER', raising=False)
    with pytest.raises(RuntimeError, match='only on Render'):
        serve_render.create_render_app()


def test_render_port(monkeypatch):
    monkeypatch.setenv('PORT', '12457')
    options = serve_render.server_options()
    assert options['host'] == '0.0.0.0'
    assert options['port'] == 12457
