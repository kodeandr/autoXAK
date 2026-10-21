"""The HTTPS switch must retain the existing certificate and redirect configuration."""
import pytest
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from deploy.prepare_nginx import prepare


SITE = """server {
    server_name autoxak.ru www.autoxak.ru;
    client_max_body_size 50M;
    location / {
        proxy_pass https://127.0.0.1:8000;
        proxy_ssl_verify off;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    listen 443 ssl;
    ssl_certificate /etc/letsencrypt/live/autoxak.ru/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/autoxak.ru/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
}
server {
    listen 80;
    server_name autoxak.ru www.autoxak.ru;
    return 301 https://$host$request_uri;
}
"""


def test_nginx_switch_retains_tls_and_http_redirect():
    staged = prepare(SITE)
    for line in SITE.splitlines():
        if any(value in line for value in ("ssl_certificate", "ssl_dhparam", "include ", "listen ", "return 301", "server_name", "X-Forwarded-Proto")):
            assert line in staged
    assert "proxy_pass http://127.0.0.1:8000;" in staged
    assert "proxy_pass https://127.0.0.1:8000;" not in staged
    assert "proxy_ssl_verify" not in staged
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in staged
    assert "client_max_body_size 150m;" in staged
    assert "proxy_read_timeout 180s;" in staged


@pytest.mark.parametrize("source", [
    SITE.replace("autoxak.ru", "another-site.ru"),
    SITE.replace("https://127.0.0.1:8000", "http://127.0.0.1:9000"),
    SITE.replace("50M", "10M"),
    SITE.replace("fullchain.pem", "another.pem"),
    SITE.replace("proxy_pass https://127.0.0.1:8000;", "proxy_pass https://127.0.0.1:8000;\nproxy_pass https://127.0.0.1:8000;"),
])
def test_nginx_switch_rejects_an_unexpected_site(source):
    with pytest.raises(ValueError):
        prepare(source)


@pytest.mark.skipif(os.name != "posix" or not shutil.which("bash"), reason="Requires Bash")
@pytest.mark.parametrize("failure", ["", "build", "dump", "startup", "stop-new", "nginx", "https"])
def test_migration_failure_preserves_old_stack_and_site(tmp_path, failure):
    """Exercise shell error handling with injected Docker/Nginx failures, without a real server."""
    root = Path(__file__).resolve().parents[1]
    scripts = tmp_path / "deploy"
    scripts.mkdir()
    for name in ("prepare_nginx.py", "production.sh", "migrate_autoxak.sh"):
        shutil.copyfile(root / "deploy" / name, scripts / name)
    site = tmp_path / "nginx.site"
    site.write_text(SITE)
    migration = scripts / "migrate_autoxak.sh"
    # Adapt privileged host paths and the root guard to this isolated fixture.
    source = migration.read_text().replace('if [[ "$EUID" != 0 ]]; then', "if false; then")
    source = source.replace("/run/lock/autoxak-production-migration.lock", str(tmp_path / "lock"))
    source = source.replace("/etc/nginx/sites-enabled/autoxak", str(site))
    migration.write_text(source)
    (tmp_path / ".env.production").write_text("# Private fixture already prepared\n")
    commands = tmp_path / "commands"
    commands.mkdir()
    log = tmp_path / "commands.jsonl"
    dispatcher = commands / "dispatcher"
    dispatcher.write_text(f"#!{sys.executable}\n" + r'''
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['COMMAND_LOG'], 'a') as log:
    log.write(json.dumps([name, *args]) + '\n')
failure = os.environ['FAILURE']
if name == 'git':
    print('fixture-commit')
elif name == 'docker':
    if args[0] == 'inspect':
        print('true')
    elif args[0] == 'compose' and 'config' in args and '--format' in args:
        print(json.dumps({'volumes': {'pgdata': {'name': 'autoxak_pgdata', 'external': True}},
              'services': {'api': {'environment': {'YANDEX_REDIRECT_URI': 'https://autoxak.ru/api/v1/auth/yandex/callback',
              'SECRET_KEY': 'test-private-key-for-deployment-checks', 'POSTGRES_PASSWORD': 'test-password'}}}}))
    elif args[0] == 'compose' and 'build' in args and failure == 'build':
        sys.exit(1)
    elif args[0] == 'compose' and 'up' in args and failure in ('startup', 'stop-new'):
        sys.exit(1)
    elif args[0] == 'compose' and 'stop' in args and failure == 'stop-new':
        sys.exit(1)
    elif args[0] == 'exec' and 'pg_dump' in args:
        if failure == 'dump': sys.exit(1)
        sys.stdout.write('fixture-custom-dump')
    elif args[0] == 'exec' and 'pg_restore' in args:
        assert sys.stdin.read() == 'fixture-custom-dump'
elif name == 'nginx' and failure == 'nginx' and '-c' not in args:
    if 'proxy_pass http://' in pathlib.Path(os.environ['NGINX_SITE']).read_text():
        sys.exit(1)
elif name == 'curl':
    if failure == 'https': sys.exit(22)
    print('{"status":"ready"}')
''')
    dispatcher.chmod(0o755)
    for name in ("docker", "nginx", "curl", "systemctl", "git"):
        (commands / name).symlink_to(dispatcher)
    env = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ["PATH"],
               COMMAND_LOG=str(log), FAILURE=failure, NGINX_SITE=str(site))
    result = subprocess.run(["bash", str(migration)], env=env, capture_output=True, text=True, timeout=20)
    actions = [json.loads(line) for line in log.read_text().splitlines()]
    old_stop = [index for index, action in enumerate(actions) if action[:2] == ["docker", "stop"]]
    if not failure:
        assert result.returncode == 0, result.stderr
        assert "proxy_pass http://127.0.0.1:8000;" in site.read_text()
        backups = list(tmp_path.glob("backups-migration-*/database.dump"))
        assert len(backups) == 1 and backups[0].read_text() == "fixture-custom-dump"
    else:
        assert result.returncode != 0
        assert site.read_text() == SITE
        if failure == "build":
            assert not old_stop
        else:
            assert old_stop
            new_stop = next(i for i, action in enumerate(actions) if action[0] == "docker" and "compose" in action and "stop" in action)
            if failure == "stop-new":
                assert not any(action[:2] == ["docker", "start"] for action in actions)
            else:
                old_start = next(i for i, action in enumerate(actions) if action[:2] == ["docker", "start"])
                assert new_stop < old_start
