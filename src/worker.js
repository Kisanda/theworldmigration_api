import schema from './content-schema.json' with { type: 'json' };

const COOKIE = 'website_editor';
const BODY_LIMIT = 16 * 1024;
const MAX_ID = 9223372036854775807n;
const INTERESTS = new Set(['canada-pr', 'australia-pr', 'denmark-visa', 'uk-entrepreneur',
  'work-visa', 'investor-visa', 'student-visa', 'visitor-visa', 'attestation', 'pnp-state', 'not-sure']);
const encoder = new TextEncoder();
const controls = /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/;

class ApiError extends Error {
  constructor(status, message, details = {}, headers = {}) {
    super(message); this.status = status; this.details = details; this.headers = headers;
  }
}
const fail = (status, message, details, headers) => { throw new ApiError(status, message, details, headers); };
const json = (data, status = 200, headers = {}) => Response.json(data, { status, headers });
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const hex = bytes => Array.from(new Uint8Array(bytes), b => b.toString(16).padStart(2, '0')).join('');
const digest = async value => hex(await crypto.subtle.digest('SHA-256', encoder.encode(value)));
const random = () => hex(crypto.getRandomValues(new Uint8Array(32)));
const now = () => Math.floor(Date.now() / 1000);

// Web Crypto verification avoids an early-exit comparison of secret values.
async function equalSecret(expected, supplied) {
  const key = await crypto.subtle.importKey('raw', encoder.encode(expected), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign', 'verify']);
  const signature = await crypto.subtle.sign('HMAC', key, encoder.encode(expected));
  return crypto.subtle.verify('HMAC', key, signature, encoder.encode(supplied));
}

function allowedOrigins(env) {
  const origins = String(env.ALLOWED_ORIGINS || '').split(',').map(s => s.trim()).filter(Boolean);
  if (!origins.length) fail(503, 'Set ALLOWED_ORIGINS to the frontend HTTPS origin.');
  for (const origin of origins) {
    let parsed;
    try { parsed = new URL(origin); } catch { fail(503, 'Invalid ALLOWED_ORIGINS configuration.'); }
    const local = ['localhost', '127.0.0.1', '[::1]'].includes(parsed.hostname);
    if (parsed.origin !== origin || parsed.username || parsed.password || origin.includes('*') ||
        (parsed.protocol !== 'https:' && !(local && parsed.protocol === 'http:'))) {
      fail(503, 'ALLOWED_ORIGINS must contain exact HTTPS origins without paths or wildcards.');
    }
  }
  return origins;
}

async function payload(request, allowed) {
  if (request.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !== 'application/json') fail(415, 'Send application/json.');
  if (Number(request.headers.get('content-length')) > BODY_LIMIT) fail(413, 'Request is too large.');
  if (!request.body) fail(400, 'A JSON object is required.');
  const reader = request.body.getReader();
  const chunks = []; let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > BODY_LIMIT) { await reader.cancel(); fail(413, 'Request is too large.'); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  let data;
  try { data = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
  catch { fail(400, 'Invalid JSON.'); }
  if (!object(data) || Object.keys(data).some(key => !allowed.includes(key))) fail(400, 'Unexpected fields.');
  return data;
}

function textField(data, field, min, max, errors) {
  let value = data[field] ?? (min === 0 ? '' : null);
  if (typeof value !== 'string') { errors[field] = 'Must be text.'; return ''; }
  value = value.trim();
  if (value.length < min || value.length > max || controls.test(value)) errors[field] = `Use ${min} to ${max} characters without control characters.`;
  return value;
}

// Atomic, shared fixed-window counters. No in-memory state or Redis is required.
async function limit(db, client, scope, rules) {
  const timestamp = now();
  for (const [seconds, maximum] of rules) {
    const window = Math.floor(timestamp / seconds) * seconds;
    const key = `${scope}:${seconds}:${window}:${client}`;
    const result = await db.prepare(`INSERT INTO rate_limits (key, attempts, expires) VALUES (?, 1, ?)
      ON CONFLICT(key) DO UPDATE SET attempts = attempts + 1 RETURNING attempts`).bind(key, window + seconds).first();
    if (result.attempts > maximum) fail(429, 'Too many requests. Please wait and retry.', {}, { 'Retry-After': String(window + seconds - timestamp) });
  }
}

function cookieToken(request) {
  const pair = (request.headers.get('cookie') || '').split(';').map(s => s.trim()).find(s => s.startsWith(`${COOKIE}=`));
  const token = pair?.slice(COOKIE.length + 1) || '';
  return /^[a-f0-9]{64}$/.test(token) ? token : '';
}
function sessionCookie(token, request, remove = false) {
  const secure = new URL(request.url).protocol === 'https:' ? '; Secure' : '';
  return `${COOKIE}=${token}; Path=/api/editor; HttpOnly; SameSite=Strict; Max-Age=${remove ? 0 : 1800}${secure}`;
}

async function editorSession(request, env, db) {
  const token = cookieToken(request);
  if (!token || !env.EDITOR_PASSWORD) fail(401, 'Sign in with your website-editor password.');
  const row = await db.prepare('SELECT * FROM editor_sessions WHERE token=?').bind(await digest(token)).first();
  if (!row || row.expires <= now() || row.credential !== await digest(env.EDITOR_PASSWORD)) fail(401, 'Sign in with your website-editor password.');
  if (!['GET', 'HEAD'].includes(request.method) && !await equalSecret(row.csrf, request.headers.get('X-CSRF-Token') || '')) fail(403, 'Invalid CSRF token.');
  return row;
}

async function page(url, db, table, fields, publicOnly = false) {
  if (Array.from(url.searchParams.keys()).some(k => !['limit', 'before'].includes(k) || url.searchParams.getAll(k).length !== 1)) fail(400, 'Invalid pagination.');
  const limitText = url.searchParams.get('limit') ?? '50';
  const before = url.searchParams.get('before') ?? String(MAX_ID);
  if (!/^\d{1,3}$/.test(limitText) || !/^\d{1,19}$/.test(before) || BigInt(before) < 1n || BigInt(before) > MAX_ID) fail(400, 'Invalid pagination.');
  const count = Number(limitText);
  if (count < 1 || count > 100) fail(400, 'Invalid pagination.');
  // Identifiers come only from the fixed route definitions below.
  const { results } = await db.prepare(`SELECT ${fields} FROM ${table} WHERE ${publicOnly ? 'approved=1 AND ' : ''}id < ? ORDER BY id DESC LIMIT ?`).bind(before, count + 1).all();
  const rows = results.slice(0, count);
  return json({ ok: true, [table]: rows, next_cursor: results.length > count ? rows.at(-1).id : null });
}

function validImage(value) {
  if (!value || /^\/(?:assets\/[A-Za-z0-9_.-]+|logo\.png)$/.test(value)) return true;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && url.host === 'images.unsplash.com' && !url.username && !url.password &&
      url.pathname.startsWith('/photo-') && !url.hash && !/[\s\x00-\x1f]/.test(value);
  } catch { return false; }
}
const document = row => ({ draft: JSON.parse(row.draft), published: JSON.parse(row.published), revision: row.revision });

async function contentDocuments(db) {
  const { results } = await db.prepare('SELECT * FROM site_content').all();
  return json({ schema, sections: Object.fromEntries(results.filter(r => Object.hasOwn(schema, r.section)).map(r => [r.section, document(r)])) });
}

async function updateContent(request, db, section, publish) {
  if (!Object.hasOwn(schema, section)) fail(404, 'Section not found.');
  const data = await payload(request, publish ? ['revision'] : ['revision', 'values']);
  if (!Number.isSafeInteger(data.revision) || data.revision < 0 || data.revision >= Number.MAX_SAFE_INTEGER) fail(400, 'Invalid revision.');
  let row;
  if (publish) {
    row = await db.prepare('UPDATE site_content SET published=draft,revision=revision+1 WHERE section=? AND revision=? RETURNING *').bind(section, data.revision).first();
  } else {
    const fields = schema[section].fields;
    if (!object(data.values) || Object.keys(data.values).length !== fields.length || fields.some(f => !Object.hasOwn(data.values, f.key))) fail(400, 'Send all fields for this section.');
    const errors = {};
    for (const field of fields) {
      const value = data.values[field.key];
      const optional = field.optional || field.default === '' || ['image', 'url'].includes(field.type);
      if (typeof value !== 'string' || value.length > field.max_length || (!optional && !value.trim()) || controls.test(value)) errors[field.key] = 'Enter text within the displayed length limit.';
      else if (field.type === 'image' && !validImage(value)) errors[field.key] = 'Use an /assets/ image path or an https://images.unsplash.com/photo-… URL.';
    }
    if (Object.keys(errors).length) fail(400, 'Check the fields.', { errors });
    row = await db.prepare('UPDATE site_content SET draft=?,revision=revision+1 WHERE section=? AND revision=? RETURNING *').bind(JSON.stringify(data.values), section, data.revision).first();
  }
  if (!row) fail(409, 'Content changed in another editor. Reload before saving.');
  return json({ ok: true, section: document(row) });
}

async function route(request, env, db, client) {
  const url = new URL(request.url);
  const path = url.pathname;
  const method = request.method === 'HEAD' ? 'GET' : request.method;
  if (url.searchParams.has('key') || url.searchParams.has('admin_key')) fail(400, 'Credentials in URLs are not accepted.');
  await limit(db, client, 'general', [[60, 120], [3600, 2000]]);

  if (path === '/api/submissions' || path.startsWith('/api/admin/')) {
    const supplied = request.headers.get('X-Admin-Key') || '';
    if (supplied.length > 128 || !await equalSecret(env.ADMIN_API_KEY, supplied)) {
      await limit(db, client, 'admin-auth', [[60, 10]]);
      fail(401, 'Unauthorized');
    }
  }
  let session;
  if (path.startsWith('/api/editor/') && path !== '/api/editor/login') session = await editorSession(request, env, db);

  if (method === 'GET' && (path === '/' || path === '/api/health')) {
    await db.prepare('SELECT section FROM site_content LIMIT 1').first();
    return json({ status: 'ok' });
  }
  if (method === 'POST' && path === '/api/contact') {
    await limit(db, client, 'contact', [[60, 5], [3600, 20]]);
    const data = await payload(request, ['name', 'email', 'phone', 'interest', 'message']);
    const errors = {};
    const name = textField(data, 'name', 2, 100, errors);
    const email = textField(data, 'email', 3, 254, errors);
    const phone = textField(data, 'phone', 7, 32, errors);
    const interest = textField(data, 'interest', 1, 40, errors);
    const message = textField(data, 'message', 0, 4000, errors);
    if (!/^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$/.test(email)) errors.email = 'Enter a valid email.';
    const digits = phone.replace(/\D/g, '').length;
    if (!/^\+?[0-9 ()\-.]{7,32}$/.test(phone) || digits < 7 || digits > 15) errors.phone = 'Enter a valid phone number.';
    if (!INTERESTS.has(interest)) errors.interest = 'Choose a program.';
    if (Object.keys(errors).length) fail(400, 'Check the fields.', { errors });
    await db.prepare('INSERT INTO submissions(name,email,phone,interest,message,created_at) VALUES(?,?,?,?,?,?)').bind(name, email, phone, interest, message, new Date().toISOString()).run();
    return json({ ok: true, message: 'Thank you. We will reach out within one business day.' }, 201);
  }
  if (method === 'GET' && path === '/api/submissions') return page(url, db, 'submissions', 'id,name,email,phone,interest,message,created_at');
  if (method === 'GET' && path === '/api/reviews') return page(url, db, 'reviews', 'id,name,rating,body,created_at', true);
  if (method === 'GET' && path === '/api/admin/reviews') return page(url, db, 'reviews', 'id,name,rating,body,approved,created_at');
  if (method === 'POST' && path === '/api/reviews') {
    await limit(db, client, 'reviews', [[60, 3], [3600, 10]]);
    const data = await payload(request, ['name', 'rating', 'body']);
    const errors = {};
    const name = textField(data, 'name', 2, 100, errors);
    const body = textField(data, 'body', 10, 4000, errors);
    if (!Number.isInteger(data.rating) || data.rating < 1 || data.rating > 5) errors.rating = 'Choose an integer rating between 1 and 5.';
    if (Object.keys(errors).length) fail(400, 'Check the fields.', { errors });
    await db.prepare('INSERT INTO reviews(name,rating,body,approved,created_at) VALUES(?,?,?,0,?)').bind(name, data.rating, body, new Date().toISOString()).run();
    return json({ ok: true, message: 'Thank you! Your review has been submitted for approval.' }, 201);
  }
  const reviewMatch = /^\/api\/admin\/reviews\/(\d{1,19})(?:\/(publish|unpublish|delete))?$/.exec(path);
  if (reviewMatch) {
    const [, id, action] = reviewMatch;
    if (BigInt(id) < 1n || BigInt(id) > MAX_ID) fail(404, 'Review not found.');
    let approved = null; let remove = false;
    if (method === 'PATCH' && !action) {
      const data = await payload(request, ['approved']);
      if (typeof data.approved !== 'boolean') fail(400, 'approved must be true or false.');
      approved = data.approved;
    } else if (method === 'POST' && ['publish', 'unpublish'].includes(action)) approved = action === 'publish';
    else if ((method === 'DELETE' && !action) || (method === 'POST' && action === 'delete')) remove = true;
    else fail(405, 'Method not allowed.');
    const result = remove
      ? await db.prepare('DELETE FROM reviews WHERE id=?').bind(id).run()
      : await db.prepare('UPDATE reviews SET approved=? WHERE id=?').bind(Number(approved), id).run();
    if (!result.meta.changes) fail(404, 'Review not found.');
    return json({ ok: true, approved });
  }
  if (method === 'GET' && path === '/api/content') {
    const { results } = await db.prepare('SELECT section,published FROM site_content').all();
    return json({ content: Object.assign({}, ...results.filter(r => Object.hasOwn(schema, r.section)).map(r => JSON.parse(r.published))) });
  }
  if (method === 'GET' && ['/api/editor/content', '/api/admin/content'].includes(path)) return contentDocuments(db);
  const contentMatch = /^\/api\/(?:editor|admin)\/content\/([a-zA-Z0-9_-]+)(\/publish)?$/.exec(path);
  if (contentMatch && ((method === 'PATCH' && !contentMatch[2]) || (method === 'POST' && contentMatch[2]))) return updateContent(request, db, contentMatch[1], Boolean(contentMatch[2]));
  if (method === 'POST' && path === '/api/editor/login') {
    await limit(db, client, 'editor-login', [[60, 5], [3600, 20]]);
    const data = await payload(request, ['password']);
    if (typeof env.EDITOR_PASSWORD !== 'string' || env.EDITOR_PASSWORD.length < 12 || env.EDITOR_PASSWORD.length > 128) fail(503, 'Website editor login has not been configured.');
    if (typeof data.password !== 'string' || data.password.length < 12 || data.password.length > 128 || !await equalSecret(env.EDITOR_PASSWORD, data.password)) fail(401, 'Incorrect editor password.');
    const token = random(); const csrf = random(); const expires = now() + 1800;
    await db.batch([
      db.prepare('DELETE FROM editor_sessions WHERE expires<=? OR token=?').bind(now(), await digest(cookieToken(request))),
      db.prepare('INSERT INTO editor_sessions(token,csrf,expires,credential) VALUES(?,?,?,?)').bind(await digest(token), csrf, expires, await digest(env.EDITOR_PASSWORD)),
    ]);
    return json({ csrf, expires }, 200, { 'Set-Cookie': sessionCookie(token, request) });
  }
  if (method === 'GET' && path === '/api/editor/session') return json({ csrf: session.csrf, expires: session.expires });
  if (method === 'POST' && path === '/api/editor/logout') {
    await db.prepare('DELETE FROM editor_sessions WHERE token=?').bind(session.token).run();
    return json({ ok: true }, 200, { 'Set-Cookie': sessionCookie('', request, true) });
  }
  fail(404, 'Not found.');
}

function secureResponse(response, request, origins, requestId) {
  const headers = new Headers(response.headers);
  for (const [key, value] of Object.entries({
    'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
    'Referrer-Policy': 'no-referrer', 'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
    'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'; base-uri 'none'", 'X-Request-ID': requestId,
  })) headers.set(key, value);
  if (new URL(request.url).protocol === 'https:') headers.set('Strict-Transport-Security', 'max-age=31536000');
  const origin = request.headers.get('Origin');
  headers.set('Vary', 'Origin');
  if (origin && origins.includes(origin)) {
    headers.set('Access-Control-Allow-Origin', origin);
    headers.set('Access-Control-Allow-Credentials', 'true');
    headers.set('Access-Control-Allow-Methods', 'GET, HEAD, POST, PATCH, DELETE, OPTIONS');
    headers.set('Access-Control-Allow-Headers', 'Content-Type, X-Admin-Key, X-CSRF-Token');
  }
  return new Response(request.method === 'HEAD' ? null : response.body, { status: response.status, headers });
}

export default {
  async fetch(request, env) {
    const requestId = crypto.randomUUID(); let origins = []; let response;
    try {
      origins = allowedOrigins(env);
      const url = new URL(request.url);
      if (url.protocol !== 'https:' && !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)) fail(400, 'HTTPS is required.');
      const origin = request.headers.get('Origin');
      if (origin && !origins.includes(origin)) fail(403, 'Origin is not allowed.');
      if (!/^[!-~]{12,128}$/.test(env.ADMIN_API_KEY || '')) fail(503, 'Configure ADMIN_API_KEY as a Cloudflare Worker secret.');
      if (!env.DB) fail(503, 'Configure the DB database binding.');
      if (request.method === 'OPTIONS') response = new Response(null, { status: 204 });
      else {
        // Cloudflare supplies this header. Do not trust caller-supplied X-Forwarded-For.
        const ip = request.headers.get('CF-Connecting-IP') || 'local';
        const client = await digest(`${env.ADMIN_API_KEY}:${ip}`);
        response = await route(request, env, env.DB, client);
      }
    } catch (error) {
      if (error instanceof ApiError) response = json({ ok: false, error: error.message, ...error.details, request_id: requestId }, error.status, error.headers);
      else {
        // Never log request bodies, credentials, database rows, or error text containing SQL values.
        console.error(JSON.stringify({ event: 'internal_error', request_id: requestId, type: error?.name || 'Error' }));
        response = json({ ok: false, error: 'Service temporarily unavailable.', request_id: requestId }, 503);
      }
    }
    return secureResponse(response, request, origins, requestId);
  },
  async scheduled(_event, env) {
    await env.DB.batch([
      env.DB.prepare('DELETE FROM rate_limits WHERE expires<=?').bind(now()),
      env.DB.prepare('DELETE FROM editor_sessions WHERE expires<=?').bind(now()),
    ]);
  },
};
