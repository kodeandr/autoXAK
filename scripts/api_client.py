"""Authenticated local verification client with normal TLS certificate validation."""
import json
import os
import ssl
import urllib.parse
import urllib.request
from pathlib import Path


class LocalAPIClient:
    def __init__(self):
        self.base_url = os.getenv('AUTOXAK_BASE_URL', 'https://localhost:8000').rstrip('/')
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme not in {'http', 'https'} or (parsed.scheme == 'http' and parsed.hostname not in {'localhost', '127.0.0.1', '::1'}):
            raise ValueError('Use HTTPS, or HTTP limited to loopback for local tests')
        ca_file = os.getenv('AUTOXAK_CA_FILE') or ('cert.pem' if Path('cert.pem').is_file() else None)
        self.context = ssl.create_default_context(cafile=ca_file)
        self.token = os.getenv('AUTOXAK_TOKEN', '')
        if self.token:
            identity = self.call('/api/v1/auth/me')
        else:
            identity = self.call('/api/v1/auth/guest', 'POST', {})
            self.token = identity['token']
        self.user_id = identity['user_id']

    def call(self, endpoint, method='GET', data=None):
        headers = {'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        body = json.dumps(data, allow_nan=False).encode('utf-8') if data is not None else None
        request = urllib.request.Request(self.base_url + endpoint, data=body, headers=headers, method=method)
        with urllib.request.urlopen(request, context=self.context, timeout=30) as response:
            return json.load(response)
