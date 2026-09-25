const origin = process.argv[2];
try {
  const url = new URL(origin);
  if (url.origin !== origin || url.protocol !== 'https:' || url.username || url.password) throw new Error();
  console.log(JSON.stringify({
    '$schema': 'https://openapi.vercel.sh/vercel.json',
    framework: 'vite', installCommand: 'npm ci', buildCommand: 'npm run build', outputDirectory: 'dist',
    rewrites: [
      { source: '/api/:path*', destination: `${origin}/api/:path*` },
      { source: '/edit', destination: '/index.html' },
      { source: '/edit/', destination: '/index.html' },
    ],
  }, null, 2));
} catch {
  console.error('Usage: npm run connect:frontend -- https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev');
  process.exitCode = 1;
}
