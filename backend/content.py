"""Section-based plain-text CMS. Authentication is enforced by the independent editor session guard."""
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit
from flask import abort, g, jsonify


def valid_image(value):
    if not value:
        return True
    if re.fullmatch(r'/(?:assets/[A-Za-z0-9_.-]+|logo\.png)', value):
        return True
    try:
        url = urlsplit(value)
        return (url.scheme == 'https' and url.netloc == 'images.unsplash.com'
                and url.path.startswith('/photo-') and not url.fragment
                and not any(c.isspace() or ord(c) < 32 for c in value))
    except ValueError:
        return False


def register_content(app, get_db, payload):
    schema = json.loads((Path(__file__).parent.parent/'content-schema.json').read_text(encoding='utf-8'))
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute('CREATE TABLE IF NOT EXISTS site_content (section TEXT PRIMARY KEY, draft TEXT NOT NULL, published TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0)')
        for section, spec in schema.items():
            defaults = json.dumps({f['key']: f['default'] for f in spec['fields']})
            db.execute('INSERT OR IGNORE INTO site_content(section,draft,published) VALUES(?,?,?)', (section, defaults, defaults))

    def document(row):
        return {'draft':json.loads(row['draft']), 'published':json.loads(row['published']), 'revision':row['revision']}

    @app.get('/api/content')
    def public_content():
        content = {}
        for row in get_db().execute('SELECT section,published FROM site_content').fetchall():
            if row['section'] in schema:
                content.update(json.loads(row['published']))
        return jsonify(content=content)

    @app.get('/api/editor/content')
    def admin_content():
        rows = get_db().execute('SELECT * FROM site_content').fetchall()
        return jsonify(schema=schema, sections={r['section']:document(r) for r in rows if r['section'] in schema})

    def update(section, publish):
        if section not in schema:
            abort(404)
        data = payload({'revision'} if publish else {'revision','values'})
        revision = data.get('revision')
        if type(revision) is not int or not 0 <= revision < 9223372036854775807:
            abort(400)
        db = get_db()
        if publish:
            cursor = db.execute('UPDATE site_content SET published=draft,revision=revision+1 WHERE section=? AND revision=?', (section,revision))
        else:
            values = data.get('values')
            fields = schema[section]['fields']
            if not isinstance(values,dict) or set(values) != {f['key'] for f in fields}:
                abort(400)
            errors = {}
            for field in fields:
                value = values[field['key']]
                is_optional = field.get('optional', False) or field.get('default') == '' or field['type'] in ('image', 'url')
                if (not isinstance(value,str) or len(value) > field['max_length']
                        or (not is_optional and not value.strip())
                        or (isinstance(value,str) and any(ord(c)<32 and c not in '\n\t\r' for c in value))):
                    errors[field['key']] = 'Enter text within the displayed length limit.'
                elif field['type'] == 'image' and not valid_image(value):
                    errors[field['key']] = 'Use an /assets/ image path or an https://images.unsplash.com/photo-… URL.'
            if errors:
                return jsonify(ok=False,errors=errors),400
            cursor = db.execute('UPDATE site_content SET draft=?,revision=revision+1 WHERE section=? AND revision=?',
                                (json.dumps(values),section,revision))
        if cursor.rowcount != 1:
            db.rollback()
            return jsonify(ok=False,error='Content changed in another editor. Reload before saving.'),409
        db.commit()
        row = db.execute('SELECT * FROM site_content WHERE section=?',(section,)).fetchone()
        app.logger.info(json.dumps({'event':'content_published' if publish else 'content_draft_saved',
                                    'section':section,'revision':row['revision'],'request_id':g.request_id}))
        return jsonify(ok=True,section=document(row))

    @app.patch('/api/editor/content/<section>')
    def save_content(section):
        return update(section,False)

    @app.post('/api/editor/content/<section>/publish')
    def publish_content(section):
        return update(section,True)

    @app.get('/api/admin/content')
    def admin_get_content():
        rows = get_db().execute('SELECT * FROM site_content').fetchall()
        return jsonify(schema=schema, sections={r['section']:document(r) for r in rows if r['section'] in schema})

    @app.patch('/api/admin/content/<section>')
    def admin_save_content(section):
        return update(section,False)

    @app.post('/api/admin/content/<section>/publish')
    def admin_publish_content(section):
        return update(section,True)
