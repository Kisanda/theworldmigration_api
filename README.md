# The World Migration backend for Render

This package contains the Flask API for contact submissions, reviews, administration,
and website text editing from your Expert4visas project. The target API domain is
`api.theworldmigration.com`. Website content and branding are preserved from the
original source. The frontend is not included.

## Upload and deploy

1. Extract the ZIP. Upload the contents of `theworldmigration-backend` to a private
   GitHub repository. The repository root must contain `backend/`,
   `content-schema.json`, and this README. Upload the extracted files, not the ZIP.
2. Create a Render **Key Value** instance in the same region as the web service.
   Copy its **Internal URL** for `RATELIMIT_STORAGE_URI`. The API requires this
   Redis-compatible service for production request limits.
3. In Render, select **New > Web Service** and connect the repository. Use:

   | Setting | Value |
   | --- | --- |
   | Language | Python 3 |
   | Root Directory | `backend` |
   | Build Command | `pip install -r requirements.lock` |
   | Start Command | `python serve_render.py` |
   | Health Check Path | Leave blank for the default TCP check |

4. Select a paid web-service plan supporting a persistent disk. Add a disk mounted
   at `/var/data` (1 GB is sufficient to start). This package keeps the original
   SQLite database design. Render's free web services cannot attach persistent
   disks; without one, submissions and saved content can be lost on restart.
5. Enter the environment variables below, then deploy. Review Render's displayed
   charges before creating paid resources. No hosting resources were created as
   part of preparing this ZIP.

## Render environment variables

| Key | Value |
| --- | --- |
| `APP_ENV` | `production` |
| `ALLOWED_ORIGINS` | `https://theworldmigration.com,https://www.theworldmigration.com` |
| `TRUSTED_HOSTS` | `api.theworldmigration.com` |
| `DATABASE_PATH` | `/var/data/leads.db` |
| `PROXY_HOPS` | `0` |
| `ADMIN_API_KEY` | A private random value generated using the command below |
| `RATELIMIT_STORAGE_URI` | Your Render Key Value Internal URL |
| `EDITOR_PASSWORD_HASH` | Optional password hash for the website editor |

Generate the admin key locally and save it privately:

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

To enable editor login, install the backend dependencies locally, run
`python hash_editor_password.py` from `backend/`, and paste the resulting hash into
`EDITOR_PASSWORD_HASH`. Keep the password itself in your password manager.
An empty hash disables editor login; the other API features still work.

The example environment file is a reference, not an automatically loaded config.
Do not publish private keys or put them in frontend environment variables.

Render supplies `RENDER`, `PORT`, and `RENDER_EXTERNAL_HOSTNAME`. The startup script
automatically allows the exact assigned `onrender.com` hostname, so you can test
before connecting DNS. It listens on `0.0.0.0` and Render's assigned port.
Use `PROXY_HOPS=0` with this script: Waitress handles the proxy headers itself.
The earlier generic `PROXY_HOPS=1` advice does not apply to this entrypoint.
Use `serve_render.py` only on Render's managed network; `serve.py` is the original
entrypoint for a local TLS reverse proxy.

## Test and connect the domain

After Render finishes, open `https://YOUR-SERVICE.onrender.com/api/health` using the
actual address from its dashboard. It should return `{"status":"ok"}`.
`/api/content` should return the default website content, and `/api/reviews`
should return an empty reviews list on a new database.

In the Render web service's **Settings > Custom Domains**, add
`api.theworldmigration.com`. Then add this GoDaddy DNS record:

| Type | Name | Value |
| --- | --- | --- |
| CNAME | `api` | The actual `YOUR-SERVICE.onrender.com` hostname shown by Render |

Use the hostname only, without `https://` or a path. Verify the domain in Render.
Render provisions HTTPS after DNS verification. Existing email records stay as
they are. This backend ZIP does not configure the Vercel frontend or its DNS.

## Connecting your Vercel frontend

Your original frontend calls relative `/api/...` paths. Deploying this backend
alone does not change those calls. The frontend still needs either a Vercel
`/api/*` proxy rewrite or its API calls updated to
`https://api.theworldmigration.com/api/...` for contact, reviews, admin, and editor.
For direct editor calls, use `credentials: 'include'` on all editor requests,
including login, and send the returned `X-CSRF-Token` for writes. The backend
permits credentialed editor calls only from the exact origins configured above.
The editor's Strict cookies work between the HTTPS production domain and its API
subdomain; they are not intended for direct cross-site `vercel.app` previews.

## Data and validation

This ZIP excludes the original database, virtual environment, caches, and any
private credentials. First startup creates a fresh database and loads the bundled
content schema. Existing leads, reviews, editor sessions, and previously saved CMS
edits are not migrated. The original ZIP and its database were not changed.

For local backend checks, install the dependencies plus `pytest`, then run
`python -m pytest backend -q` from the repository root. One frontend-specific test
is skipped because this is a backend-only package. The Render proxy checks use
isolated temporary databases; they do not connect to a live Render service.

Package verification: 65 tests passed and the one frontend-only test was skipped
using Python 3.14.6 and the supplied runtime dependencies. A live Render deployment
and the frontend-to-backend connection still need to be verified after hosting.

## Official references

- [Render web services](https://render.com/docs/web-services)
- [Persistent disks](https://render.com/docs/disks)
- [Render environment variables](https://render.com/docs/environment-variables)
- [Custom domains](https://render.com/docs/custom-domains)
- [Waitress proxy configuration](https://docs.pylonsproject.org/projects/waitress/en/stable/arguments.html)
