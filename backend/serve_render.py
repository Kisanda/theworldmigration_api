"""Render-only entrypoint: managed TLS proxy -> Waitress -> Flask."""
import os

from waitress import serve

from app import create_app


def create_render_app():
    # Only Render's managed network may reach this proxy-trusting server.
    if os.getenv('RENDER') != 'true':
        raise RuntimeError('Use this entrypoint only on Render; use app.py for local development.')
    if os.getenv('APP_ENV', 'production') != 'production':
        raise RuntimeError('Render deployments must use APP_ENV=production.')
    if os.getenv('PROXY_HOPS', '0') != '0':
        raise RuntimeError('Set PROXY_HOPS=0; Waitress already handles the Render proxy.')

    os.environ.setdefault('APP_ENV', 'production')
    os.environ.setdefault('PROXY_HOPS', '0')
    os.environ.setdefault('ALLOWED_ORIGINS',
                          'https://theworldmigration.com,https://www.theworldmigration.com')
    os.environ.setdefault('DATABASE_PATH', '/var/data/leads.db')
    hosts = [host.strip() for host in os.getenv(
        'TRUSTED_HOSTS', 'api.theworldmigration.com').split(',') if host.strip()]
    # The assigned hostname must work before the custom domain is connected.
    render_host = os.getenv('RENDER_EXTERNAL_HOSTNAME', '').strip()
    if render_host and render_host not in hosts:
        hosts.append(render_host)
    os.environ['TRUSTED_HOSTS'] = ','.join(hosts)
    return create_app()


def server_options():
    return {
        'host': '0.0.0.0',
        'port': int(os.getenv('PORT', '10000')),
        'threads': 4,
        'max_request_body_size': 16384,
        'max_request_header_size': 16384,
        'channel_timeout': 30,
        'connection_limit': 100,
        'ident': 'API',
        # Render terminates TLS and proxies requests over its managed network.
        # Trust only the nearest proxy; discard forwarded host/port headers.
        'trusted_proxy': '*',
        'trusted_proxy_count': 1,
        'trusted_proxy_headers': {'x-forwarded-for', 'x-forwarded-proto'},
        'clear_untrusted_proxy_headers': True,
    }


if __name__ == '__main__':
    serve(create_render_app(), **server_options())
