"""Recover legacy API DB credentials from existing containers; no SQL password changes.

Run on the server: python3 deploy/repair_legacy_db_auth.py /opt/autoxak
Credentials stay in memory/private Compose override; they are never printed.
"""
import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time
from urllib.parse import quote, unquote, urlsplit
import urllib.request


class RecoveryError(Exception):
    pass


def inspect(name):
    result=subprocess.run(['docker','inspect',name],capture_output=True,text=True)
    if result.returncode:
        raise RecoveryError('Cannot inspect container: '+name)
    return json.loads(result.stdout)[0]


def env_of(container):
    return dict(item.split('=',1) for item in container['Config'].get('Env',[]) if '=' in item)


def checked(args,**kwargs):
    result=subprocess.run(args,capture_output=True,text=True,**kwargs)
    if result.returncode:
        # Do not expose Docker/Compose messages that might include DSNs/passwords.
        raise RecoveryError('Command failed: '+' '.join(args[:3])+'; inspect service logs locally.')
    return result


def test_password(name,address,user,database,password):
    if '\n' in password or '\r' in password:
        return False
    script='IFS= read -r encoded; PGPASSWORD="$(printf %s "$encoded" | base64 -d)"; export PGPASSWORD; exec psql -h "$1" -U "$2" -d "$3" -v ON_ERROR_STOP=1 -qAt -c "SELECT 1"'
    reply=subprocess.run(['docker','exec','-i',name,'sh','-ec',script,'auth-check',address,user,database],
                         input=base64.b64encode(password.encode()).decode()+'\n',capture_output=True,text=True,timeout=15)
    return reply.returncode==0 and reply.stdout.strip()=='1'


def value_for_compose(value):
    # Compose expands dollars even in quoted YAML; $$ means a literal dollar.
    return json.dumps(value.replace('$','$$'),ensure_ascii=False)


def update_override(original,url,password):
    begin='    # BEGIN autoXAK database authentication\n'
    end='    # END autoXAK database authentication\n'
    if begin in original:
        start=original.index(begin);stop=original.index(end,start)+len(end)
        original=original[:start]+original[stop:]
    if not original.startswith('# autoXAK HTTP recovery override\n'):
        raise RecoveryError('HTTP recovery override is missing/custom; run the HTTP recovery first.')
    # Only the script-owned API block is changed. Do not create a second mapping.
    if '\n    environment:' in original:
        raise RecoveryError('A custom environment block requires manual review.')
    return original.rstrip()+'\n'+begin+'    environment:\n      DATABASE_URL: '+value_for_compose(url)+'\n      POSTGRES_PASSWORD: '+value_for_compose(password)+'\n'+end


def recover(project_dir):
    project_dir=Path(project_dir).resolve()
    api=inspect('autoXAK_backend_api');postgres=inspect('autoXAK_postgres')
    labels=api['Config'].get('Labels') or {}
    if labels.get('com.docker.compose.project')!='autoxak' or labels.get('com.docker.compose.project.working_dir')!=str(project_dir):
        raise RecoveryError('API project/path mismatch.')
    if not postgres['State']['Running']:
        raise RecoveryError('Primary PostgreSQL is stopped. Run the updated HTTP recovery first.')
    volume=next((m['Name'] for m in postgres['Mounts'] if m['Destination']=='/var/lib/postgresql/data'),None)
    if volume!='autoxak_pgdata':
        raise RecoveryError('Primary PostgreSQL volume mismatch.')
    peers=checked(['docker','ps','-q','--filter','volume='+volume]).stdout.split()
    if any(inspect(peer)['Name']!='/autoXAK_postgres' for peer in peers):
        raise RecoveryError('Another running container mounts PGDATA; stop the duplicate first.')
    pg_env=env_of(postgres);api_env=env_of(api)
    user=pg_env.get('POSTGRES_USER','postgres');database=pg_env.get('POSTGRES_DB',user)
    old_url=urlsplit(api_env.get('DATABASE_URL',''))
    if unquote(old_url.username or '')!=user or unquote(old_url.path.lstrip('/'))!=database or old_url.hostname!='postgres':
        raise RecoveryError('API target differs from the expected PostgreSQL service/role/database.')
    address=next((n['IPAddress'] for n in postgres['NetworkSettings']['Networks'].values() if n.get('IPAddress')),None)
    if not address:
        raise RecoveryError('Primary database network address is unavailable.')
    # A localhost trust rule can accept any password. Probe the bridge address
    # and require rejection of a fresh random password before trusting a match.
    if test_password('autoXAK_postgres',address,user,database,secrets.token_hex(32)):
        raise RecoveryError('TCP auth accepted an arbitrary password; pg_hba.conf requires review.')
    candidates=[unquote(old_url.password or ''),api_env.get('POSTGRES_PASSWORD',''),pg_env.get('POSTGRES_PASSWORD','')]
    try:
        previous=inspect('autoxak-production-postgres-1')
        if any(m.get('Name')==volume and m['Destination']=='/var/lib/postgresql/data' for m in previous['Mounts']):
            candidates.append(env_of(previous).get('POSTGRES_PASSWORD',''))
    except RecoveryError:
        pass
    password=None
    for candidate in dict.fromkeys(candidates):
        if candidate and test_password('autoXAK_postgres',address,user,database,candidate):
            password=candidate;break
    if password is None:
        raise RecoveryError('No stored container credential matches the database. No password/config was changed.')
    url='postgresql+asyncpg://'+quote(user,safe='')+':'+quote(password,safe='')+'@postgres:5432/'+quote(database,safe='')
    override=project_dir/'docker-compose.override.yml'
    original=override.read_text()
    revised=update_override(original,url,password)
    backup=project_dir/'backups'/('db-auth-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(4))
    backup.mkdir(mode=0o700,parents=True)
    shutil.copyfile(override,backup/override.name);os.chmod(backup/override.name,0o600)
    temporary=project_dir/('.db-auth-override-'+secrets.token_hex(8)+'.yml')
    descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(descriptor,'w') as stream:stream.write(revised)
        checked(['docker','compose','-p','autoxak','-f','docker-compose.yml','-f',str(temporary),'config','--quiet'],cwd=project_dir)
        os.replace(temporary,override)
    finally:
        temporary.unlink(missing_ok=True)
    checked(['docker','compose','-p','autoxak','up','-d','--no-deps','--no-build','--force-recreate','api'],cwd=project_dir)
    print('Stored database credential verified. API recreated; SQL password was not changed.')
    print('Private configuration backup:',backup)
    for attempt in range(45):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/ready',timeout=3) as reply:
                if json.load(reply).get('status')=='ready':
                    print('{"status":"ready"}');return
        except Exception:
            time.sleep(1)
    raise RecoveryError('API is not ready; inspect docker logs --tail=80 autoXAK_backend_api. Database credential was verified.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project_dir',nargs='?',default='/opt/autoxak')
    args=parser.parse_args()
    try:recover(args.project_dir)
    except RecoveryError as error:parser.exit(1,str(error)+'\n')
    except Exception as error:parser.exit(1,'Recovery failed ('+type(error).__name__+'); no credentials printed. Inspect locally.\n')


if __name__=='__main__':main()
