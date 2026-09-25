import { readFile, writeFile } from 'node:fs/promises';
import { createInterface } from 'node:readline/promises';
import { stdin, stdout } from 'node:process';

const rl = createInterface({ input: stdin, output: stdout });
try {
  const databaseId = (await rl.question('Paste the Cloudflare D1 database ID: ')).trim();
  if (!/^[a-f\d]{8}-(?:[a-f\d]{4}-){3}[a-f\d]{12}$/i.test(databaseId) || databaseId === '00000000-0000-0000-0000-000000000000') throw new Error('Enter the real D1 UUID from Cloudflare.');
  const origins = (await rl.question('Frontend HTTPS origin(s), comma separated, with no trailing slash: ')).split(',').map(s => s.trim()).filter(Boolean);
  if (!origins.length) throw new Error('At least one frontend origin is required.');
  for (const origin of origins) {
    const url = new URL(origin);
    if (url.origin !== origin || url.protocol !== 'https:' || url.username || url.password || origin.includes('*')) throw new Error('Use exact HTTPS origins, for example https://expert4visas.vercel.app');
  }
  const file = new URL('../wrangler.jsonc', import.meta.url);
  const config = JSON.parse(await readFile(file, 'utf8'));
  config.d1_databases[0].database_id = databaseId;
  config.vars.ALLOWED_ORIGINS = origins.join(',');
  await writeFile(file, JSON.stringify(config, null, 2) + '\n');
  console.log('Saved wrangler.jsonc. It contains no passwords and can be committed to GitHub.');
} catch (error) { console.error(error.message); process.exitCode = 1; }
finally { rl.close(); }
