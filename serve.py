"""Production WSGI entrypoint; only the local TLS reverse proxy may reach it."""
from waitress import serve
from app import create_app

if __name__ == '__main__':
    serve(create_app(), host='127.0.0.1', port=5000, threads=4,
          max_request_body_size=16384, max_request_header_size=16384,
          channel_timeout=30, connection_limit=100, ident='API',
          trusted_proxy='127.0.0.1', trusted_proxy_count=1,
          trusted_proxy_headers={'x-forwarded-for', 'x-forwarded-proto'},
          clear_untrusted_proxy_headers=True)
