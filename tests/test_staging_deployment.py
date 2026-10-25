import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest

from deploy.init_staging import initialize


def test_staging_private_config_and_repeat_protection(tmp_path):
    initialize(tmp_path, 'staging.autoxak.ru')
    for path in (tmp_path/'.env.staging', *(tmp_path/'.staging').iterdir()):
        assert path.stat().st_mode & 0o777 == 0o600
    original = (tmp_path/'.env.staging').read_bytes()
    with pytest.raises(ValueError):
        initialize(tmp_path, 'staging.autoxak.ru')
    assert (tmp_path/'.env.staging').read_bytes() == original
    site = (tmp_path/'.staging/nginx.conf').read_text()
    assert 'Secure; HttpOnly; SameSite=Strict' in site
    assert 'proxy_set_header Authorization "";' in site.split('location / {')[-2]
    assert not (tmp_path/'.env.production').exists()


@pytest.mark.parametrize('domain', ['autoxak.ru','www.autoxak.ru','https://staging.autoxak.ru','x;include /secret;'])
def test_staging_rejects_production_or_invalid_host(tmp_path, domain):
    with pytest.raises(ValueError):
        initialize(tmp_path, domain)
    assert not (tmp_path/'.env.staging').exists()


@pytest.mark.skipif(not shutil.which('docker'), reason='Compose required')
def test_rendered_staging_is_isolated(tmp_path):
    initialize(tmp_path, 'staging.autoxak.ru')
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    for key in ('POSTGRES_VOLUME','POSTGRES_VOLUME_EXTERNAL','COMPOSE_PROFILES','APP_DOMAIN',
                'SECRET_KEY','POSTGRES_PASSWORD','YANDEX_ID_SALT'):
        env.pop(key, None)
    result = subprocess.run(['docker','compose','--project-name','autoxak-staging','--env-file',str(tmp_path/'.env.staging'),
                             '-f',str(root/'compose.staging.yml'),'config','--format','json'],
                            env=env,capture_output=True,text=True,check=True)
    config = json.loads(result.stdout)
    assert config['name'] == 'autoxak-staging'
    assert set(config['services']) == {'api','postgres','redis'}
    assert config['volumes']['pgdata']['name'] == 'autoxak-staging_pgdata'
    assert not config['volumes']['pgdata'].get('external')
    api = config['services']['api']
    assert api['ports'][0]['host_ip'] == '127.0.0.1' and str(api['ports'][0]['published']) == '18000'
    assert api['environment']['POSTGRES_DB'] == 'autoXAK_staging'
    assert api['environment']['DGIS_USE_MOCK'] == 'true'
    assert 'ports' not in config['services']['postgres']
    assert 'ports' not in config['services']['redis']
    assert int(api['mem_limit']) == 768*1024**2


def test_staging_script_does_not_inherit_production_keys_or_network(tmp_path):
    root=Path(__file__).resolve().parents[1]
    (tmp_path/'deploy').mkdir()
    shutil.copy(root/'deploy/staging.sh',tmp_path/'deploy/staging.sh')
    (tmp_path/'.env.staging').write_text('fixture')
    commands=tmp_path/'bin'
    commands.mkdir()
    for name,source in {
        'git':'#!/bin/sh\necho fixture\n',
        'docker':'#!/bin/sh\ntest -z "${SECRET_KEY:-}" && test -z "${POSTGRES_PASSWORD:-}" && test -z "${PROXY_SUBNET:-}"\n'
    }.items():
        (commands/name).write_text(source)
        (commands/name).chmod(0o755)
    env=dict(os.environ,PATH=str(commands)+os.pathsep+os.environ['PATH'],
             SECRET_KEY='production-fixture',POSTGRES_PASSWORD='production-fixture',PROXY_SUBNET='172.29.250.0/24')
    subprocess.run(['bash',str(tmp_path/'deploy/staging.sh'),'status'],env=env,check=True,capture_output=True)


@pytest.mark.skipif(os.getenv('AUTOXAK_STAGING_INTEGRATION') != '1', reason='Requires live staging at localhost:18000 and cached Nginx image')
def test_real_https_gate_preserves_bearer_auth(tmp_path):
    initialize(tmp_path, 'staging.autoxak.test')
    # Only temporary fixture hashes become worker-readable, not the private config.
    (tmp_path/'users').write_text((tmp_path/'.staging/htpasswd').read_text())
    (tmp_path/'users').chmod(0o644)
    tmp_path.chmod(0o755)
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                    '-subj','/CN=staging.autoxak.test','-addext','subjectAltName=DNS:staging.autoxak.test',
                    '-keyout',str(tmp_path/'key.pem'),'-out',str(tmp_path/'cert.pem')],
                   check=True,capture_output=True)
    site=(tmp_path/'.staging/nginx.conf').read_text()
    site=site.replace('http://127.0.0.1:18000','http://api:8000')
    site=site.replace('/etc/letsencrypt/live/staging.autoxak.test/fullchain.pem','/etc/staging/cert.pem')
    site=site.replace('/etc/letsencrypt/live/staging.autoxak.test/privkey.pem','/etc/staging/key.pem')
    site=site.replace('/etc/nginx/autoxak-staging.htpasswd','/etc/staging/users')
    site='\n'.join(line for line in site.splitlines() if 'options-ssl-nginx.conf' not in line and 'ssl_dhparam' not in line)
    (tmp_path/'nginx.conf').write_text('events {}\nhttp {\n'+site+'\n}\n')
    name='autoxak-staging-gate-validation'
    subprocess.run(['docker','run','-d','--name',name,'--network','autoxak-staging_proxy',
                    '-p','127.0.0.1:18443:443','--read-only',
                    '--tmpfs','/var/cache/nginx','--tmpfs','/var/run','--tmpfs','/tmp',
                    '-v',str(tmp_path)+':/etc/staging:ro','-v',str(tmp_path/'nginx.conf')+':/etc/nginx/nginx.conf:ro',
                    'nginx:stable-alpine'],check=True,capture_output=True)
    def request(path, extra=(), config=''):
        result=subprocess.run(['curl','-q','--noproxy','*','--silent','--show-error','--max-time','5',
                               '--cacert',str(tmp_path/'cert.pem'),'--resolve','staging.autoxak.test:18443:127.0.0.1',
                               '-D',str(tmp_path/'headers'),'-o',str(tmp_path/'body'),'-w','%{http_code}',
                               '--config','-',*extra,'https://staging.autoxak.test:18443'+path],
                              input=config,capture_output=True,text=True,check=True)
        return int(result.stdout), (tmp_path/'headers').read_text(), (tmp_path/'body').read_text()
    try:
        for _ in range(30):
            try:
                code,_,_=request('/')
                break
            except subprocess.CalledProcessError:
                time.sleep(.1)
        else:
            result=subprocess.run(['docker','logs',name],capture_output=True,text=True)
            pytest.fail('Validation Nginx did not start: '+result.stderr[-3000:])
        assert code==302
        code,headers,_=request('/_staging/access')
        assert code==401 and 'set-cookie:' not in headers.lower()
        code,headers,_=request('/_staging/access',config='user="tester:incorrect"\n')
        assert code==401 and 'set-cookie:' not in headers.lower()
        password=(tmp_path/'.staging/access.txt').read_text().split('Password: ')[1].strip()
        code,headers,_=request('/_staging/access',config=f'user="tester:{password}"\n')
        assert code==200
        cookie=next(line.split(': ',1)[1].split(';',1)[0] for line in headers.splitlines() if line.lower().startswith('set-cookie:'))
        assert 'Secure; HttpOnly; SameSite=Strict' in headers
        opts=['-H','Cookie: '+cookie]
        code,headers,_=request('/',opts)
        assert code==200 and 'noindex' in headers
        code,_,_=request('/static/session.js',opts)
        assert code==200
        code,_,body=request('/api/v1/auth/guest',opts+['-X','POST','-H','Content-Type: application/json','--data','{}'])
        assert code==200
        token=json.loads(body)['token']
        code,_,_=request('/api/v1/auth/me',opts+['-H','Authorization: Bearer '+token])
        assert code==200
        code,_,_=request('/api/v1/auth/me',['-H','Authorization: Bearer '+token])
        assert code==302
        code,_,_=request('/', ['-H','Cookie: autoxak_staging_gate=invalid'])
        assert code==302
    finally:
        subprocess.run(['docker','rm','-f',name],check=True,capture_output=True)
