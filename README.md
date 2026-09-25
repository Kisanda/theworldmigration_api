# Expert4visas API — Cloudflare Workers + D1

This is the Cloudflare version of the backend from your Expert4visas ZIP. It uses a JavaScript Worker and a Cloudflare D1 database, replacing the original Flask/local SQLite/Redis hosting requirements. It preserves the frontend's API paths for enquiries, reviews, admin moderation, and the inline content editor.

The site remains on Vercel. GitHub stores this backend's source code. Cloudflare runs the API and stores new data in D1.

## Before you start

- Have a Cloudflare account, a GitHub account, and your frontend's Vercel URL.
- Install Node.js 24 LTS if you will use the terminal setup below. The included contract tests need Node 22.13 or newer.
- Extract this ZIP. Upload the files inside it to GitHub, not the ZIP itself.
- This package starts a NEW database with default website content. Existing enquiries, reviews, editor sessions, and previously published database edits are NOT copied from your original database.

## Option A: GitHub and Cloudflare dashboard setup

### 1. Create the database

In Cloudflare, open **Storage & databases → D1 SQL Database**, create a database named `expert4visas-db`, and copy its database ID.

Open the extracted `wrangler.jsonc` in a text editor and change:

- `database_id`: replace the all-zero value with your actual D1 database ID.
- `ALLOWED_ORIGINS`: replace `https://YOUR-FRONTEND.vercel.app` with your exact frontend origin, without a trailing slash. If you also use a custom domain, include both origins separated by commas.

The database ID and website origin are configuration values, not passwords. Never put the admin key or editor password in this file.

### 2. Upload the backend to GitHub

1. Open https://github.com/new and create a repository named `expert4visas-api`. A private repository works. Initialize it with a README to make the upload menu easy to find.
2. Select **Add file → Upload files**, or **uploading an existing file** if the repository is empty.
3. Drag in the extracted contents, including `src`, `migrations`, `scripts`, `test`, `package.json`, `package-lock.json`, and `wrangler.jsonc`.
4. Commit the files. Make sure `package.json` and `wrangler.jsonc` appear at the top level.

Do not upload a ZIP, `node_modules`, `.dev.vars`, `.env`, or any customer database.

### 3. Connect Cloudflare to GitHub

In **Workers & Pages**, create/import a Worker from your GitHub repository. Use **Workers**, because this package is an API Worker.

| Setting | Value |
| --- | --- |
| Worker name | `expert4visas-api` (must match `wrangler.jsonc`) |
| Repository | Your `expert4visas-api` repository |
| Production branch | `main`, or the actual branch you uploaded |
| Root directory | Repository root |
| Build command | `npm test` |
| Deploy command | `npm run deploy` |
| Node version | 24 (set build variable `NODE_VERSION=24` if needed) |

Cloudflare installs dependencies from `package-lock.json`. The deploy command applies D1 migrations and then deploys the Worker. The migration creates tables and seeds the default content only when missing; later deployments preserve existing records.

**Database permission:** the build's Cloudflare API token needs **Account → D1 → Edit** as well as Worker deployment permissions for your account. Cloudflare's automatically created build token may omit D1 permissions. Edit that token under **My Profile → API Tokens**, or choose a custom build token with the required permissions. A migration permission error usually means this permission is missing.

Disable non-production/preview builds for this initial setup; this configuration has one production database. Use a separate D1 database before enabling previews or staging.

### 4. Add the two runtime secrets

After the first deployment, open your Worker → **Settings → Variables and Secrets** and add both as **Secret** values:

| Secret name | Purpose |
| --- | --- |
| `ADMIN_API_KEY` | Sign-in key for `/admin.html`. Use a new random value of at least 32 characters. |
| `EDITOR_PASSWORD` | Separate password for `/edit`. Use a strong value of 12–128 characters, ideally a new random value. |

Save/deploy the secret changes. These must be Worker runtime secrets, not build-only secrets. Until `ADMIN_API_KEY` is configured, the API deliberately responds with 503. Without `EDITOR_PASSWORD`, editor login remains disabled.

You can generate two random values locally with `npm run secrets:generate`. Keep them in your password manager. This Worker uses `EDITOR_PASSWORD`; the original Python `EDITOR_PASSWORD_HASH` is not used.

Open the Worker URL with `/api/health` appended. It should return `{"status":"ok"}`.

## Option B: First deployment from your computer

Open a terminal inside the extracted backend folder. Run each command separately:

```text
npm ci
npx wrangler login
npx wrangler d1 create expert4visas-db
npm run configure
npm test
npm run deploy
npm run secrets:generate
npx wrangler secret put ADMIN_API_KEY
npx wrangler secret put EDITOR_PASSWORD
```

The configure command asks for the database ID from `d1 create` and your frontend origin. The two secret commands prompt for the generated values. The initial deploy can complete before secrets are set, but the API will return 503 until its admin secret is configured.

Record the `https://expert4visas-api.<your-subdomain>.workers.dev` address shown by Wrangler. Open that address with `/api/health` appended.

Then upload the configured files to GitHub. In your existing Worker's **Settings → Builds**, connect that repository and use the build settings in Option A. Future production-branch pushes will redeploy automatically. Do not create a second Worker with a different name.

If Git is installed and you prefer pushing from the terminal, create an EMPTY GitHub repository, then run these inside this extracted folder, replacing the example repository URL with yours:

```text
git init
git add .
git commit -m "Add Cloudflare API and D1 database setup"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/expert4visas-api.git
git push -u origin main
```

## Connect your Vercel frontend

Your existing frontend already calls `/api/...`. Add a Vercel external rewrite so those requests reach Cloudflare while editor cookies remain on your frontend's origin.

In your FRONTEND GitHub repository, replace `vercel.json` with the following, replacing the Worker hostname with your actual hostname:

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "framework": "vite",
  "installCommand": "npm ci",
  "buildCommand": "npm run build",
  "outputDirectory": "dist",
  "rewrites": [
    {
      "source": "/api/:path*",
      "destination": "https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev/api/:path*"
    },
    { "source": "/edit", "destination": "/index.html" },
    { "source": "/edit/", "destination": "/index.html" }
  ]
}
```

Or print the exact configuration with:

```text
npm run connect:frontend -- https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev
```

Copy only the printed JSON into the frontend's `vercel.json`. Commit it and wait for Vercel to redeploy. Keep the API rewrite first if you later add a general page fallback. No React code or public `VITE_` secret is required.

Check `https://YOUR-FRONTEND.vercel.app/api/health`. Then test a contact submission, review submission/moderation through `/admin.html`, and editor draft/save/publish/logout through `/edit` on the FRONTEND website.

The Worker URL does not serve the frontend or `admin.html`. Use the frontend website for those pages.

## API coverage

| Route | Access / function |
| --- | --- |
| `GET /api/health` | Health and database readiness |
| `POST /api/contact` | Save an enquiry |
| `GET /api/submissions` | Admin key required; paginated enquiries |
| `GET /api/reviews` | Approved reviews only |
| `POST /api/reviews` | Submit a review for approval |
| `GET /api/admin/reviews` | Admin key required; all reviews |
| `PATCH /api/admin/reviews/:id` | Set `approved` to true/false |
| `POST /api/admin/reviews/:id/publish` | Publish a review |
| `POST /api/admin/reviews/:id/unpublish` | Hide a review |
| `DELETE /api/admin/reviews/:id` | Delete a review |
| `POST /api/admin/reviews/:id/delete` | Existing frontend deletion fallback |
| `GET /api/content` | Published content only |
| `GET /api/admin/content` | Admin view of content |
| `PATCH /api/admin/content/:section` | Admin draft save with revision check |
| `POST /api/admin/content/:section/publish` | Admin publication |
| `POST /api/editor/login` | Password login; scoped session cookie |
| `GET /api/editor/session` | Session and CSRF token |
| `GET /api/editor/content` | Editor view of drafts and published content |
| `PATCH /api/editor/content/:section` | Editor draft save with revision and CSRF checks |
| `POST /api/editor/content/:section/publish` | Editor publication with revision and CSRF checks |
| `POST /api/editor/logout` | Revoke session and clear cookie |

## Operational details

- D1 provides persistent storage. Local SQLite files and Redis are not used by this version.
- Rate limits use atomic fixed-window D1 counters, with short-term and hourly limits. This differs from the original Redis moving-window algorithm and permits bursts at window boundaries. An hourly scheduled task removes expired counters and sessions.
- Rate limiting uses Cloudflare's observed connecting IP. With Vercel external rewrites, multiple visitors may share a proxy IP and therefore share limits. The app intentionally does not trust arbitrary forwarded-IP headers. If shared limits cause 429 responses under normal traffic, use a trusted signed proxy integration before raising limits or changing client-IP handling.
- Editor sessions last 30 minutes. Cookies are HttpOnly, Secure on HTTPS, scoped to `/api/editor`, and SameSite=Strict. Mutations require a CSRF token. Rotating `EDITOR_PASSWORD` invalidates existing sessions.
- Enquiries are saved for viewing in the admin page. This package does not add email delivery; the original backend did not send enquiry emails either.
- Existing personal/customer data was intentionally excluded from the distributable. Importing existing data is a separate migration. Back up D1 before changing or importing production data.
- `ALLOWED_ORIGINS` is maintained in `wrangler.jsonc`. If your frontend URL changes, update it in GitHub and redeploy.
- Keep secrets in Cloudflare runtime secret storage. Do not expose them as Vercel `VITE_` variables or commit them to GitHub.

## Local development and verification

Copy `.dev.vars.example` to `.dev.vars`, fill in separate local-only secrets, then run:

```text
npm ci
npm run db:local
npm run dev
```

To use the original frontend's development proxy, start the Worker on port 5000 with `npx wrangler dev --port 5000`. The frontend runs on port 5175.

Run `npm test` for the included API contract tests and `npm run check` for a Wrangler dry-run bundle check. `TEST-RESULTS.md` describes the checks actually completed when this ZIP was prepared.

## Official documentation

- D1 setup: https://developers.cloudflare.com/d1/get-started/
- D1 migrations: https://developers.cloudflare.com/d1/reference/migrations/
- GitHub-connected Workers Builds: https://developers.cloudflare.com/workers/ci-cd/builds/
- Build commands and token permissions: https://developers.cloudflare.com/workers/ci-cd/builds/configuration/
- Runtime secrets: https://developers.cloudflare.com/workers/configuration/secrets/
- Vercel external rewrites: https://vercel.com/docs/routing/rewrites
