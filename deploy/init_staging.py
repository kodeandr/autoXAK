"""Create private staging credentials and Nginx candidates, never production files."""
import argparse
import os
from pathlib import Path
import re
import secrets
import subprocess


def initialize(root, domain):
    if not re.fullmatch(r"(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}", domain):
        raise ValueError("Use a hostname, not a URL")
    if domain.lower() in {"autoxak.ru", "www.autoxak.ru"}:
        raise ValueError("Staging must use a separate hostname")
    config = root / '.env.staging'
    private = root / '.staging'
    if config.exists() or private.exists():
        raise ValueError("Staging configuration already exists; preserve its keys")
    password = secrets.token_hex(16)
    hashed = subprocess.run(['openssl','passwd','-6','-stdin'], input=password+'\n',
                            capture_output=True,text=True,check=True).stdout.strip()
    if not hashed.startswith('$6$'):
        raise ValueError("Password hashing failed")
    # 128-bit gate secret also fits Nginx's default 64-byte map hash bucket.
    token = secrets.token_hex(16)
    private.mkdir(mode=0o700)
    def write(path, body):
        fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        with os.fdopen(fd,'w') as output:
            output.write(body)
    write(config, f'APP_DOMAIN={domain}\nSECRET_KEY={secrets.token_hex(32)}\n'
          f'POSTGRES_PASSWORD={secrets.token_hex(32)}\nYANDEX_ID_SALT={secrets.token_hex(32)}\n'
          'DGIS_API_KEY=\nYANDEX_CLIENT_ID=\nYANDEX_CLIENT_SECRET=\n')
    write(private/'access.txt', 'Username: tester\nPassword: '+password+'\n')
    write(private/'htpasswd', 'tester:'+hashed+'\n')
    http = f'''server {{
    listen 80;
    server_name {domain};
    location /.well-known/acme-challenge/ {{ root /var/www/autoxak-staging-acme; }}
    location / {{ return 404; }}
}}
'''
    write(private/'nginx-http.conf', http)
    tls = f'''# Include from http context (Ubuntu sites-enabled).
map $cookie_autoxak_staging_gate $autoxak_staging_allowed {{
    default 0;
    "{token}" 1;
}}
server {{
    listen 443 ssl;
    server_name {domain};
    ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
    client_max_body_size 150m;
    add_header X-Robots-Tag "noindex, nofollow, noarchive" always;
    location = /_staging/access {{
        auth_basic "autoXAK staging";
        auth_basic_user_file /etc/nginx/autoxak-staging.htpasswd;
        proxy_pass http://127.0.0.1:18000/health;
        proxy_set_header Authorization "";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        add_header Set-Cookie "autoxak_staging_gate={token}; Path=/; Max-Age=86400; Secure; HttpOnly; SameSite=Strict";
        add_header Refresh "0; url=/";
        add_header Cache-Control "no-store";
        add_header X-Robots-Tag "noindex, nofollow, noarchive" always;
    }}
    location / {{
        if ($autoxak_staging_allowed = 0) {{ return 302 /_staging/access; }}
        proxy_pass http://127.0.0.1:18000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 5s;
        proxy_read_timeout 180s;
        proxy_send_timeout 180s;
    }}
}}
'''
    write(private/'nginx.conf', http.replace('return 404;', 'return 301 https://$host$request_uri;')+tls)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('domain')
    args = parser.parse_args()
    initialize(Path(__file__).resolve().parents[1], args.domain)
    print('Created .env.staging and .staging/ (private). Login details: .staging/access.txt')
