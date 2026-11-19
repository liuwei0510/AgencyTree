"""Shared connection settings for local and remote ComfyUI requests."""
import os
from urllib.parse import urlparse


def connection_mode(url, mode='auto'):
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('comfy.base_url must be an HTTP(S) service URL without credentials, query or fragment')
    if mode not in ('auto', 'local', 'remote'):
        raise ValueError('comfy.mode must be auto, local or remote')
    if mode == 'auto':
        return 'local' if parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1', '::1') and parsed.path in ('', '/') else 'remote'
    return mode


def connection_options(config):
    options = config.get('comfy') or {}
    headers = {}
    for header, env in options.get('headers_env', {}).items():
        value = os.environ.get(env)
        if not value:
            raise ValueError(f'Missing environment variable for ComfyUI header: {env}')
        headers[header] = value
    env = options.get('api_key_env')
    if env:
        value = os.environ.get(env)
        if not value:
            raise ValueError(f'Missing ComfyUI API token environment variable: {env}')
        headers['Authorization'] = 'Bearer ' + value
    return {'headers': headers}
