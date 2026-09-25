import { randomBytes } from 'node:crypto';
console.log('Save these two separate values in your password manager. Do not commit them to GitHub.');
console.log('ADMIN_API_KEY=' + randomBytes(32).toString('base64url'));
console.log('EDITOR_PASSWORD=' + randomBytes(32).toString('base64url'));
console.log('Enter each value when its wrangler secret put command prompts you.');
