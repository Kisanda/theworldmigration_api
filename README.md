# Expert4visas backend

Flask/SQLite API for enquiries, moderated reviews, and website content editing.
This folder is a standalone Git repository root: app.py and requirements.lock
are at the top level. The companion UI is supplied in Expert4visas-frontend.zip.

## Run locally (PowerShell)

Use Python 3.12+ (the original project used Python 3.14). From this folder:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.lock
$env:APP_ENV = 'development'
$env:ADMIN_API_KEY = & .venv/Scripts/python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
.venv/Scripts/python.exe app.py
```

The API listens on http://127.0.0.1:5000. The generated admin key remains in the
current shell environment. Use that value to sign in at the companion frontend's
/admin.html. Configure a persistent private key through your service environment
if it must survive restarts. No default admin key is included.

To enable /edit, run the following helper, enter a new editor password when
prompted, and configure its printed hash as EDITOR_PASSWORD_HASH before starting
the API. Keep the hash literal when assigning it in PowerShell (single quotes).

```powershell
.venv/Scripts/python.exe hash_editor_password.py
```

.env.example documents configuration only. The application does not automatically
load .env files. Configure variables in your shell, service manager, or secret store.
The database is created at instance/leads.db unless DATABASE_PATH is set to an
absolute path. Existing leads, reviews, editor sessions, and saved website content
are not included. Source defaults are loaded from content-schema.json.

## Tests

```powershell
.venv/Scripts/python.exe -m pip install pytest
.venv/Scripts/python.exe -m pytest -q
```

Backend tests use temporary databases. One existing source check concerns frontend
admin files: it runs if Expert4visas-frontend/public exists next to this repository
and is explicitly skipped when the backend is extracted alone.

## Production configuration

Run `python serve.py` behind an HTTPS reverse proxy. Configure APP_ENV=production,
ADMIN_API_KEY, exact TRUSTED_HOSTS and ALLOWED_ORIGINS, an absolute private
DATABASE_PATH, and a private Redis RATELIMIT_STORAGE_URI. Configure
EDITOR_PASSWORD_HASH to enable website editing. Keep PROXY_HOPS=0 with serve.py.
The included deploy/ files are templates; replace the example domain, certificate
paths, static web root, and service paths for your host. Serve only the frontend's
dist/ folder as static content and forward /api/ to this service on the same origin.
The API rejects plain HTTP in production and requires Redis for rate limiting.

Protect the admin UI and private APIs with your access gateway, configure private
database permissions and backups, and validate proxy/TLS/Redis on the deployment
host. Keep original data backups private and migrate the current live database
separately; this archive is source code, not a database backup.

## GitHub and packaging

Commit this folder's contents, including requirements.lock, .env.example,
content-schema.json, tests, and .gitignore. Do not commit actual environment files,
keys, password hashes, databases, logs, virtual environments, or caches.
Keep content-schema.json synchronized with the frontend copy.

The original backend directory was moved to this repository root. Its schema path
and README reference were adjusted; the frontend source test now locates the
companion repository and skips when absent. The password helper was converted from
Windows-1252 to UTF-8 so Python can read it. Other application logic is unchanged.
The original credential-bearing launcher, database, dependencies, caches, and old
validation reports were omitted. This packaging does not perform a security audit
or certify production readiness. Replace credentials from the original launcher
before deploying this source.
