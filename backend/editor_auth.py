"""Independent, scoped, revocable website editor sessions."""
import hashlib
import hmac
import secrets
import sqlite3
import time
from flask import abort, g, jsonify, request
from werkzeug.security import check_password_hash

COOKIE = 'website_editor'
def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def register_editor_auth(app, get_db, payload):
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute('CREATE TABLE IF NOT EXISTS editor_sessions (token TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires INTEGER NOT NULL, credential TEXT NOT NULL)')

    @app.before_request
    def editor_guard():
        if not request.path.startswith('/api/editor/') or request.method == 'OPTIONS':
            return
        if request.method not in ('GET', 'HEAD') and request.headers.get('Origin') not in ([None] + app.config.get('ALLOWED_ORIGINS', [])):
            abort(403)
        if request.path == '/api/editor/login':
            return
        token = request.cookies.get(COOKIE, '')
        row = get_db().execute('SELECT * FROM editor_sessions WHERE token=?', (digest(token),)).fetchone()
        configured = app.config['EDITOR_PASSWORD_HASH']
        if not configured or not row or row['expires'] <= time.time() or row['credential'] != digest(configured):
            abort(401)
        if request.method not in ('GET', 'HEAD') and not hmac.compare_digest(row['csrf'], request.headers.get('X-CSRF-Token', '')):
            abort(403)
        g.editor_session = row

    @app.post('/api/editor/login')
    @app.extensions['security_limiter'].limit('5 per minute;20 per hour')
    def editor_login():
        password = payload({'password'}).get('password')
        configured = app.config['EDITOR_PASSWORD_HASH']
        if not configured:
            return jsonify(error='Website editor login has not been configured.'), 503
        if not isinstance(password, str) or not 12 <= len(password) <= 128:
            abort(401)
        try:
            valid = check_password_hash(configured, password)
        except (ValueError, TypeError):
            valid = False
        if not valid:
            abort(401)
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        expires = int(time.time()) + 1800
        db = get_db()
        db.execute('DELETE FROM editor_sessions WHERE expires<=? OR token=?', (int(time.time()), digest(request.cookies.get(COOKIE, ''))))
        db.execute('INSERT INTO editor_sessions VALUES(?,?,?,?)', (digest(token), csrf, expires, digest(configured)))
        db.commit()
        response = jsonify(csrf=csrf, expires=expires)
        response.set_cookie(COOKIE, token, max_age=1800, httponly=True, secure=app.config['APP_ENV']=='production', samesite='Strict', path='/api/editor')
        return response

    @app.get('/api/editor/session')
    def editor_session():
        return jsonify(csrf=g.editor_session['csrf'], expires=g.editor_session['expires'])

    @app.post('/api/editor/logout')
    def editor_logout():
        db = get_db()
        db.execute('DELETE FROM editor_sessions WHERE token=?', (g.editor_session['token'],))
        db.commit()
        response = jsonify(ok=True)
        response.delete_cookie(COOKIE, path='/api/editor', httponly=True, secure=app.config['APP_ENV']=='production', samesite='Strict')
        return response
