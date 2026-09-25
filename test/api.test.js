import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';
import worker from '../src/worker.js';
import schema from '../src/content-schema.json' with { type: 'json' };

// Executes real SQLite statements behind the D1 interface. These are local
// contract tests, not a substitute for testing a deployed Cloudflare Worker.
function database() {
  const sql = new DatabaseSync(':memory:');
  sql.exec(readFileSync(new URL('../migrations/0001_initial.sql', import.meta.url), 'utf8'));
  const db = {
    prepare(query) {
      let params = [];
      const statement = {
        bind(...values) { params = values; return statement; },
        async first() { return sql.prepare(query).get(...params) || null; },
        async all() { return { results: sql.prepare(query).all(...params) }; },
        async run() { return { success: true, meta: sql.prepare(query).run(...params) }; },
      };
      return statement;
    },
    async batch(statements) {
      sql.exec('BEGIN');
      try { const result = []; for (const statement of statements) result.push(await statement.run()); sql.exec('COMMIT'); return result; }
      catch (error) { sql.exec('ROLLBACK'); throw error; }
    },
  };
  return { db, sql };
}
function fixture(t) {
  const { db, sql } = database(); t.after(() => sql.close());
  const env = { DB: db, ADMIN_API_KEY: 'test-admin-secret-ONLY-for-tests-12345', EDITOR_PASSWORD: 'test-editor-secret-ONLY-for-tests', ALLOWED_ORIGINS: 'https://site.example' };
  const call = (path, options = {}) => {
    const { body, headers = {}, ...rest } = options;
    return worker.fetch(new Request(`https://api.example${path}`, {
      ...rest, headers: { Origin: 'https://site.example', 'CF-Connecting-IP': '192.0.2.1', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...headers },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    }), env);
  };
  return { env, sql, call, admin: { 'X-Admin-Key': env.ADMIN_API_KEY } };
}
const contact = { name: 'Test Person', email: 'person@example.test', phone: '+94 77 1234567', interest: 'canada-pr', message: 'A test enquiry.' };
const review = { name: 'Test Person', rating: 5, body: 'A useful and friendly service.' };
async function login(f) {
  const response = await f.call('/api/editor/login', { method: 'POST', body: { password: f.env.EDITOR_PASSWORD } });
  assert.equal(response.status, 200);
  return { cookie: response.headers.get('set-cookie').split(';')[0], csrf: (await response.json()).csrf, response };
}

test('health and security headers, HEAD, exact-origin preflight', async t => {
  const f = fixture(t);
  const res = await f.call('/api/health');
  assert.equal(res.status, 200); assert.deepEqual(await res.json(), { status: 'ok' });
  assert.equal(res.headers.get('cache-control'), 'no-store');
  assert.equal(res.headers.get('access-control-allow-origin'), 'https://site.example');
  assert.equal(res.headers.get('x-content-type-options'), 'nosniff');
  assert.equal(await (await f.call('/api/health', { method: 'HEAD' })).text(), '');
  assert.equal((await f.call('/api/contact', { method: 'OPTIONS' })).status, 204);
});
test('disallowed origins, HTTP, and query credentials are rejected', async t => {
  const f = fixture(t);
  assert.equal((await f.call('/api/health', { headers: { Origin: 'https://evil.example' } })).status, 403);
  assert.equal((await f.call('/api/submissions?admin_key=anything')).status, 400);
  assert.equal((await worker.fetch(new Request('http://api.example/api/health'), f.env)).status, 400);
});
test('missing production configuration fails closed', async t => {
  const f = fixture(t); f.env.ADMIN_API_KEY = '';
  assert.equal((await f.call('/api/submissions')).status, 503);
  f.env.ADMIN_API_KEY = 'test-key-valid-12345'; f.env.ALLOWED_ORIGINS = '*';
  assert.equal((await f.call('/api/health')).status, 503);
});
test('contact is saved and visible only with the admin key', async t => {
  const f = fixture(t);
  assert.equal((await f.call('/api/contact', { method: 'POST', body: contact })).status, 201);
  assert.equal((await f.call('/api/submissions')).status, 401);
  const data = await (await f.call('/api/submissions', { headers: f.admin })).json();
  assert.equal(data.submissions[0].email, contact.email);
  assert.equal(data.submissions[0].message, contact.message);
});
test('invalid contact fields and unexpected payload keys do not create records', async t => {
  const f = fixture(t);
  for (const body of [{ ...contact, email: 'invalid' }, { ...contact, phone: 'abc12345' }, { ...contact, interest: 'invented' }, { ...contact, name: 5 }, { ...contact, admin: true }]) {
    assert.equal((await f.call('/api/contact', { method: 'POST', body })).status, 400);
  }
  assert.equal(f.sql.prepare('SELECT count(*) AS n FROM submissions').get().n, 0);
});
test('oversized, malformed and non-object JSON is rejected', async t => {
  const f = fixture(t);
  assert.equal((await f.call('/api/contact', { method: 'POST', body: { ...contact, message: 'a'.repeat(17000) } })).status, 413);
  assert.equal((await f.call('/api/contact', { method: 'POST', body: [] })).status, 400);
  assert.equal((await worker.fetch(new Request('https://api.example/api/contact', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{' }), f.env)).status, 400);
  assert.equal((await worker.fetch(new Request('https://api.example/api/contact', { method: 'POST', body: 'hello' }), f.env)).status, 415);
});
test('contact rate limits persist in the shared database', async t => {
  const f = fixture(t);
  for (let i = 0; i < 5; i++) assert.equal((await f.call('/api/contact', { method: 'POST', body: contact })).status, 201);
  const blocked = await f.call('/api/contact', { method: 'POST', body: contact });
  assert.equal(blocked.status, 429); assert.ok(Number(blocked.headers.get('retry-after')) > 0);
  assert.equal((await f.call('/api/contact', { method: 'POST', body: contact, headers: { 'CF-Connecting-IP': '192.0.2.2' } })).status, 201);
});
test('admin brute force attempts are throttled', async t => {
  const f = fixture(t);
  for (let i = 0; i < 10; i++) assert.equal((await f.call('/api/submissions')).status, 401);
  assert.equal((await f.call('/api/submissions')).status, 429);
});
test('reviews stay private until moderated; publish, unpublish and delete work', async t => {
  const f = fixture(t);
  assert.equal((await f.call('/api/reviews', { method: 'POST', body: review })).status, 201);
  assert.equal((await (await f.call('/api/reviews')).json()).reviews.length, 0);
  const all = await (await f.call('/api/admin/reviews', { headers: f.admin })).json();
  const path = `/api/admin/reviews/${all.reviews[0].id}`;
  assert.equal((await f.call(path + '/publish', { method: 'POST' })).status, 401);
  assert.equal((await f.call(path + '/publish', { method: 'POST', headers: f.admin })).status, 200);
  assert.equal((await (await f.call('/api/reviews')).json()).reviews.length, 1);
  assert.equal((await f.call(path, { method: 'PATCH', headers: f.admin, body: { approved: false } })).status, 200);
  assert.equal((await (await f.call('/api/reviews')).json()).reviews.length, 0);
  assert.equal((await f.call(path, { method: 'DELETE', headers: f.admin })).status, 200);
  assert.equal((await f.call(path + '/publish', { method: 'POST', headers: f.admin })).status, 404);
});
test('review rating must be a whole number and review throttling is enforced', async t => {
  const f = fixture(t);
  for (const rating of [true, 2.5, 6]) assert.equal((await f.call('/api/reviews', { method: 'POST', body: { ...review, rating } })).status, 400);
  assert.equal((await f.call('/api/reviews', { method: 'POST', body: review })).status, 429);
});
test('pagination has stable cursors and rejects duplicate/invalid parameters', async t => {
  const f = fixture(t);
  for (let i = 0; i < 3; i++) await f.call('/api/contact', { method: 'POST', body: { ...contact, name: `Person ${i}` } });
  const first = await (await f.call('/api/submissions?limit=2', { headers: f.admin })).json();
  assert.equal(first.submissions.length, 2); assert.ok(first.next_cursor);
  const next = await (await f.call(`/api/submissions?limit=2&before=${first.next_cursor}`, { headers: f.admin })).json();
  assert.equal(next.submissions.length, 1); assert.equal(next.next_cursor, null);
  for (const query of ['limit=0', 'limit=101', 'limit=2&limit=3', 'before=0', 'before=9223372036854775808', 'x=1']) assert.equal((await f.call(`/api/reviews?${query}`)).status, 400);
});
test('editor login sets scoped secure cookies; session stores a hash not the token', async t => {
  const f = fixture(t); const session = await login(f);
  const cookie = session.response.headers.get('set-cookie');
  assert.match(cookie, /HttpOnly/); assert.match(cookie, /Secure/); assert.match(cookie, /SameSite=Strict/); assert.match(cookie, /Path=\/api\/editor/);
  assert.notEqual(f.sql.prepare('SELECT token FROM editor_sessions').get().token, session.cookie.split('=')[1]);
  assert.equal((await f.call('/api/editor/session', { headers: { Cookie: session.cookie } })).status, 200);
  assert.equal((await f.call('/api/editor/session')).status, 401);
});
test('editor login failures are throttled', async t => {
  const f = fixture(t);
  for (let i = 0; i < 5; i++) assert.equal((await f.call('/api/editor/login', { method: 'POST', body: { password: 'incorrect-secret-1234' } })).status, 401);
  assert.equal((await f.call('/api/editor/login', { method: 'POST', body: { password: f.env.EDITOR_PASSWORD } })).status, 429);
});
test('drafts remain private until publish; stale revisions return conflict', async t => {
  const f = fixture(t); const session = await login(f);
  const headers = { Cookie: session.cookie, 'X-CSRF-Token': session.csrf };
  const sections = (await (await f.call('/api/editor/content', { headers })).json()).sections;
  const [name, spec] = Object.entries(schema)[0];
  const field = spec.fields.find(field => field.type !== 'image');
  const original = sections[name].draft[field.key];
  const values = { ...sections[name].draft, [field.key]: 'Updated' };
  const path = `/api/editor/content/${name}`;
  assert.equal((await f.call(path, { method: 'PATCH', headers: { Cookie: session.cookie }, body: { revision: 0, values } })).status, 403);
  let response = await f.call(path, { method: 'PATCH', headers, body: { revision: 0, values } });
  assert.equal(response.status, 200); const saved = (await response.json()).section;
  assert.equal((await (await f.call('/api/content')).json()).content[field.key], original);
  assert.equal((await f.call(path, { method: 'PATCH', headers, body: { revision: 0, values } })).status, 409);
  response = await f.call(path + '/publish', { method: 'POST', headers, body: { revision: saved.revision } });
  assert.equal(response.status, 200);
  assert.equal((await (await f.call('/api/content')).json()).content[field.key], 'Updated');
});
test('content validation blocks unknown fields and unsafe image addresses', async t => {
  const f = fixture(t);
  const sections = (await (await f.call('/api/admin/content', { headers: f.admin })).json()).sections;
  const [name, spec] = Object.entries(schema).find(([, s]) => s.fields.some(field => field.type === 'image'));
  const image = spec.fields.find(field => field.type === 'image');
  const values = { ...sections[name].draft, [image.key]: 'javascript:alert(1)' };
  assert.equal((await f.call(`/api/admin/content/${name}`, { method: 'PATCH', headers: f.admin, body: { revision: 0, values } })).status, 400);
  assert.equal((await f.call(`/api/admin/content/${name}`, { method: 'PATCH', headers: f.admin, body: { revision: 0, values: {} } })).status, 400);
  assert.equal((await f.call('/api/admin/content/unknown', { method: 'PATCH', headers: f.admin, body: { revision: 0, values: {} } })).status, 404);
});
test('concurrent saves allow one writer and preserve revision integrity', async t => {
  const f = fixture(t); const name = Object.keys(schema)[0];
  const values = Object.fromEntries(schema[name].fields.map(field => [field.key, field.default]));
  const statuses = await Promise.all([1, 2].map(async () => (await f.call(`/api/admin/content/${name}`, { method: 'PATCH', headers: f.admin, body: { revision: 0, values } })).status));
  assert.deepEqual(statuses.sort(), [200, 409]);
});
test('logout, expiry and password rotation invalidate editor sessions', async t => {
  const f = fixture(t); let session = await login(f);
  assert.equal((await f.call('/api/editor/logout', { method: 'POST', headers: { Cookie: session.cookie, 'X-CSRF-Token': session.csrf }, body: {} })).status, 200);
  assert.equal((await f.call('/api/editor/session', { headers: { Cookie: session.cookie } })).status, 401);
  session = await login(f); f.sql.exec('UPDATE editor_sessions SET expires=0');
  assert.equal((await f.call('/api/editor/session', { headers: { Cookie: session.cookie } })).status, 401);
  session = await login(f); f.env.EDITOR_PASSWORD = 'a-new-rotated-editor-password';
  assert.equal((await f.call('/api/editor/session', { headers: { Cookie: session.cookie } })).status, 401);
});
test('scheduled cleanup removes only expired counters and sessions', async t => {
  const f = fixture(t); await login(f);
  f.sql.exec("INSERT INTO rate_limits VALUES ('old',1,0); INSERT INTO editor_sessions VALUES ('old','old',0,'old');");
  await worker.scheduled({}, f.env);
  assert.equal(f.sql.prepare("SELECT count(*) AS n FROM rate_limits WHERE key='old'").get().n, 0);
  assert.equal(f.sql.prepare('SELECT count(*) AS n FROM editor_sessions').get().n, 1);
});
