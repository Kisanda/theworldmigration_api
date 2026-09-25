"""Contact/review API. Production configuration fails closed; see README.md."""
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

from flask import Flask, abort, g, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

INTERESTS = {'canada-pr', 'australia-pr', 'denmark-visa', 'uk-entrepreneur',
             'work-visa', 'investor-visa', 'student-visa', 'visitor-visa',
             'attestation', 'pnp-state', 'not-sure'}
MAX_ID = 9223372036854775807


def create_app(overrides=None):
    app = Flask(__name__, static_folder=None)
    env = os.getenv('APP_ENV', 'production')
    app.config.update(
        EDITOR_PASSWORD_HASH=os.getenv('EDITOR_PASSWORD_HASH', ''),
        APP_ENV=env, ADMIN_API_KEY=os.getenv('ADMIN_API_KEY', ''),
        DATABASE=os.getenv('DATABASE_PATH', str(Path(__file__).parent / 'instance' / 'leads.db')),
        ALLOWED_ORIGINS=[s.strip() for s in os.getenv('ALLOWED_ORIGINS', '').split(',') if s.strip()],
        TRUSTED_HOSTS=[s.strip() for s in os.getenv('TRUSTED_HOSTS', '').split(',') if s.strip()],
        RATELIMIT_STORAGE_URI=os.getenv('RATELIMIT_STORAGE_URI', 'memory://'),
        RATELIMIT_HEADERS_ENABLED=True, RATELIMIT_SWALLOW_ERRORS=False,
        MAX_CONTENT_LENGTH=16 * 1024, MAX_FORM_MEMORY_SIZE=16 * 1024,
        MAX_FORM_PARTS=10, PROXY_HOPS=int(os.getenv('PROXY_HOPS', '0')),
    )
    if overrides:
        app.config.update(overrides)
    production = app.config['APP_ENV'] == 'production'
    if app.config['APP_ENV'] not in {'production', 'development', 'testing'}:
        raise RuntimeError('APP_ENV must be production, development or testing')
    key = app.config['ADMIN_API_KEY']
    if not re.fullmatch(r'[!-~]{12,128}', key):
        raise RuntimeError('Set ADMIN_API_KEY to 12–128 printable ASCII characters without spaces')
    key_digest = hashlib.sha256(key.encode()).digest()
    if not app.config['TRUSTED_HOSTS']:
        if production:
            raise RuntimeError('TRUSTED_HOSTS is required in production')
        app.config['TRUSTED_HOSTS'] = ['localhost', '127.0.0.1']
    if not app.config['ALLOWED_ORIGINS'] and not production:
        app.config['ALLOWED_ORIGINS'] = ['http://localhost:5175', 'http://127.0.0.1:5175']
    if production and not app.config['ALLOWED_ORIGINS']:
        raise RuntimeError('ALLOWED_ORIGINS must include the public HTTPS site origin')
    for origin in app.config['ALLOWED_ORIGINS']:
        parsed = urlsplit(origin)
        if (parsed.scheme not in ({'https'} if production else {'http', 'https'})
                or not parsed.hostname or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment or '*' in origin):
            raise RuntimeError('ALLOWED_ORIGINS must contain exact origins without paths or wildcards')
    if production and not app.config['RATELIMIT_STORAGE_URI'].startswith(('redis://', 'rediss://')):
        raise RuntimeError('Production requires a shared Redis rate-limit store')
    if app.config['PROXY_HOPS'] not in (0, 1):
        raise RuntimeError('Only zero or one trusted reverse proxy is supported')
    if app.config['PROXY_HOPS']:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=0, x_port=0, x_prefix=0)
    db_path = Path(app.config['DATABASE'])
    if not db_path.is_absolute():
        raise RuntimeError('DATABASE_PATH must be absolute')
    db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                email TEXT NOT NULL, phone TEXT NOT NULL, interest TEXT NOT NULL,
                message TEXT, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5), body TEXT NOT NULL,
                approved INTEGER NOT NULL DEFAULT 0 CHECK(approved IN (0,1)), created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS reviews_public ON reviews(approved, id);
        ''')
    if os.name != 'nt':
        db_path.chmod(0o600)

    @app.before_request
    def request_context():
        g.request_id = uuid.uuid4().hex
        g.started = time.monotonic()
        if production and not request.is_secure:
            abort(400, 'HTTPS is required')
        origin = request.headers.get('Origin')
        if origin and origin not in app.config['ALLOWED_ORIGINS']:
            abort(403, 'Origin is not allowed')
        if request.args.keys() & {'key', 'admin_key'}:
            abort(400, 'Credentials in URLs are not accepted')

    limiter = Limiter(key_func=get_remote_address, app=app,
                      application_limits=['300 per minute', '4000 per hour'],
                      default_limits=['120 per minute', '2000 per hour'],
                      strategy='moving-window')
    app.extensions['security_limiter'] = limiter

    @app.before_request
    def protect_admin():
        if request.method == 'OPTIONS':
            return None
        if request.path == '/api/submissions' or request.path.startswith('/api/admin/'):
            # Header only: cookies, query strings and legacy keys never authenticate.
            supplied = request.headers.get('X-Admin-Key', '')
            digest = hashlib.sha256(supplied.encode()).digest()
            if len(supplied) > 128 or not hmac.compare_digest(digest, key_digest):
                with limiter.limit('10 per minute', scope='admin-auth', key_func=get_remote_address):
                    abort(401, 'Unauthorized')

    @app.after_request
    def secure_response(response):
        response.headers.update({
            'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
            'Referrer-Policy': 'no-referrer', 'Cache-Control': 'no-store',
            'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
            'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
            'X-Request-ID': getattr(g, 'request_id', uuid.uuid4().hex),
        })
        if production:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        origin = request.headers.get('Origin')
        if origin in app.config['ALLOWED_ORIGINS']:
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PATCH, DELETE, OPTIONS'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Admin-Key, X-CSRF-Token'
            response.vary.add('Origin')
        # No bodies, query strings, credentials or client details in application logs.
        app.logger.info(json.dumps({
            'event': 'http_request', 'request_id': response.headers['X-Request-ID'],
            'endpoint': request.endpoint or 'unmatched', 'method': request.method,
            'status': response.status_code,
            'duration_ms': round((time.monotonic() - getattr(g, 'started', time.monotonic())) * 1000),
        }))
        return response

    @app.errorhandler(HTTPException)
    def http_error(error):
        response = error.get_response()
        response.data = app.json.dumps({'ok': False, 'error': error.name,
                                        'request_id': getattr(g, 'request_id', None)})
        response.content_type = 'application/json'
        return response

    @app.errorhandler(Exception)
    def unexpected_error(error):
        app.logger.error(json.dumps({'event': 'internal_error',
                                     'type': type(error).__name__,
                                     'request_id': getattr(g, 'request_id', None)}))
        return jsonify(ok=False, error='Service temporarily unavailable',
                       request_id=getattr(g, 'request_id', None)), 503

    app.logger.setLevel(logging.INFO)

    def get_db():
        if 'db' not in g:
            g.db = sqlite3.connect(app.config['DATABASE'], timeout=5)
            g.db.row_factory = sqlite3.Row
            g.db.execute('PRAGMA foreign_keys=ON')
        return g.db

    @app.teardown_appcontext
    def close_db(_error):
        db = g.pop('db', None)
        if db is not None:
            db.close()

    def payload(fields):
        if request.mimetype != 'application/json':
            abort(415)
        data = request.get_json()
        if not isinstance(data, dict) or data.keys() - fields:
            abort(400)
        return data

    def text_field(data, name, minimum, maximum, errors):
        value = data.get(name, '' if minimum == 0 else None)
        if not isinstance(value, str):
            errors[name] = 'Must be text.'
            return ''
        value = value.strip()
        if not minimum <= len(value) <= maximum or any(ord(c) < 32 and c not in '\n\r\t' for c in value):
            errors[name] = f'Use {minimum} to {maximum} characters without control characters.'
        return value

    def page(table, fields, public=False):
        if request.args.keys() - {'limit', 'before'}:
            abort(400)
        try:
            if any(len(request.args.getlist(k)) != 1 for k in request.args):
                raise ValueError()
            limit = int(request.args.get('limit', '50'))
            before = int(request.args.get('before', str(MAX_ID)))
            if not 1 <= limit <= 100 or not 1 <= before <= MAX_ID:
                raise ValueError()
        except ValueError:
            abort(400)
        # Table/column identifiers are fixed application constants, never user input.
        where = 'approved=1 AND ' if public else ''
        rows = get_db().execute(
            f'SELECT {fields} FROM {table} WHERE {where}id < ? ORDER BY id DESC LIMIT ?',
            (before, limit + 1)).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        return jsonify(ok=True, **{table: [dict(r) for r in rows]},
                       next_cursor=rows[-1]['id'] if more else None)

    @app.get('/')
    @app.get('/api/health')
    def health():
        return jsonify(status='ok')

    @app.post('/api/contact')
    @limiter.limit('5 per minute; 20 per hour')
    def contact():
        data = payload({'name', 'email', 'phone', 'interest', 'message'})
        errors = {}
        name = text_field(data, 'name', 2, 100, errors)
        email = text_field(data, 'email', 3, 254, errors)
        phone = text_field(data, 'phone', 7, 32, errors)
        interest = text_field(data, 'interest', 1, 40, errors)
        message = text_field(data, 'message', 0, 4000, errors)
        if not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', email):
            errors['email'] = 'Enter a valid email.'
        if not re.fullmatch(r'\+?[0-9 ()\-.]{7,32}', phone) or not 7 <= len(re.sub(r'\D', '', phone)) <= 15:
            errors['phone'] = 'Enter a valid phone number.'
        if interest not in INTERESTS:
            errors['interest'] = 'Choose a program.'
        if errors:
            return jsonify(ok=False, errors=errors), 400
        db = get_db()
        db.execute('INSERT INTO submissions (name,email,phone,interest,message,created_at) VALUES (?,?,?,?,?,?)',
                   (name, email, phone, interest, message, datetime.now(timezone.utc).isoformat()))
        db.commit()
        return jsonify(ok=True, message="Thank you. We will reach out within one business day."), 201

    @app.get('/api/submissions')
    def submissions():
        return page('submissions', 'id,name,email,phone,interest,message,created_at')

    @app.get('/api/reviews')
    def reviews():
        return page('reviews', 'id,name,rating,body,created_at', public=True)

    @app.post('/api/reviews')
    @limiter.limit('3 per minute; 10 per hour')
    def submit_review():
        data = payload({'name', 'rating', 'body'})
        errors = {}
        name = text_field(data, 'name', 2, 100, errors)
        body = text_field(data, 'body', 10, 4000, errors)
        rating = data.get('rating')
        if type(rating) is not int or not 1 <= rating <= 5:
            errors['rating'] = 'Choose an integer rating between 1 and 5.'
        if errors:
            return jsonify(ok=False, errors=errors), 400
        db = get_db()
        db.execute('INSERT INTO reviews (name,rating,body,approved,created_at) VALUES (?,?,?,0,?)',
                   (name, rating, body, datetime.now(timezone.utc).isoformat()))
        db.commit()
        return jsonify(ok=True, message='Thank you! Your review has been submitted for approval.'), 201

    @app.get('/api/admin/reviews')
    def admin_reviews():
        return page('reviews', 'id,name,rating,body,approved,created_at')

    def moderate(review_id, state=None, delete=False):
        if not 1 <= review_id <= MAX_ID:
            abort(404)
        db = get_db()
        if delete:
            cursor = db.execute('DELETE FROM reviews WHERE id=?', (review_id,))
        else:
            cursor = db.execute('UPDATE reviews SET approved=? WHERE id=?', (int(state), review_id))
        if not cursor.rowcount:
            abort(404)
        db.commit()
        app.logger.info(json.dumps({'event': 'review_deleted' if delete else 'review_moderated',
                                    'review_id': review_id, 'approved': state,
                                    'request_id': g.request_id}))
        return jsonify(ok=True, approved=state)

    @app.patch('/api/admin/reviews/<int:review_id>')
    def patch_review(review_id):
        data = payload({'approved'})
        if type(data.get('approved')) is not bool:
            abort(400)
        return moderate(review_id, data['approved'])

    @app.post('/api/admin/reviews/<int:review_id>/publish')
    def publish_review(review_id):
        return moderate(review_id, True)

    @app.post('/api/admin/reviews/<int:review_id>/unpublish')
    def unpublish_review(review_id):
        return moderate(review_id, False)

    @app.delete('/api/admin/reviews/<int:review_id>')
    @app.post('/api/admin/reviews/<int:review_id>/delete')
    def delete_review(review_id):
        return moderate(review_id, delete=True)

    from editor_auth import register_editor_auth
    register_editor_auth(app, get_db, payload)
    from content import register_content
    register_content(app, get_db, payload)
    return app


if __name__ == '__main__':
    create_app().run(host='127.0.0.1', port=5000, debug=False)
