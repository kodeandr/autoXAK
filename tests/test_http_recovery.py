"""Validate HTTP-only recovery without touching the user's live Docker daemon."""
import json
import os
from pathlib import Path
import subprocess


def recover(tmp_path, fail_backup=False):
    project=tmp_path/'autoxak'; project.mkdir()
    (project/'docker-compose.yml').write_text('services: {}\n')
    commands=tmp_path/'bin';commands.mkdir()
    docker=commands/'docker'
    docker.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['CALLS'],'a') as f:f.write(json.dumps(args)+'\\n')
if args[0]=='inspect':
 fmt=args[-1]
 if 'working_dir' in fmt: print(os.environ['PROJECT'])
 elif 'project"' in fmt: print('autoxak')
 elif '.Destination' in fmt: print('autoxak_pgdata')
 elif fmt=='{{.Name}}': print('/autoXAK_postgres' if args[1]=='primary' else '/autoxak-production-postgres-1')
 elif '.Gateway' in fmt: print('172.20.0.1,127.0.0.1')
 else: print('[]')
elif args[0]=='ps': print('primary\\nduplicate')
elif args[0]=='exec' and 'pg_dump' in args[-1]:
 if os.environ.get('FAIL_BACKUP')=='yes':sys.exit(1)
 sys.stdout.buffer.write(b'PGDMP-binary-test')
''');docker.chmod(0o755)
    curl=commands/'curl';curl.write_text('#!/bin/sh\nprintf \'{"status":"ready"}\'\n');curl.chmod(0o755)
    calls=tmp_path/'calls'
    env=dict(os.environ,PATH=str(commands)+os.pathsep+os.environ['PATH'],CALLS=str(calls),PROJECT=str(project),FAIL_BACKUP='yes' if fail_backup else 'no')
    result=subprocess.run(['bash','deploy/repair_legacy_http.sh',str(project)],env=env,capture_output=True,text=True,timeout=20)
    return result,project,[json.loads(s) for s in calls.read_text().splitlines()]


def test_recovery_stops_only_duplicate_and_recreates_only_api(tmp_path):
    result,project,calls=recover(tmp_path)
    assert result.returncode==0,result.stdout+result.stderr
    assert ['stop','--time','60','duplicate'] in calls
    assert not any(c[:2]==['stop','primary'] for c in calls)
    up=next(c for c in calls if c[0]=='compose' and 'up' in c)
    assert up[-1]=='api' and '--no-deps' in up and '--no-build' in up
    assert not any('down' in c for c in calls)
    override=(project/'docker-compose.override.yml').read_text()
    assert '--ssl' not in override and '--reload' not in override
    assert '127.0.0.1:8000:8000' in override
    assert next(project.glob('backups/*/database.dump')).read_bytes()==b'PGDMP-binary-test'


def test_failed_backup_prevents_api_recreation(tmp_path):
    result,project,calls=recover(tmp_path,fail_backup=True)
    assert result.returncode!=0
    assert not any(c[0]=='compose' and 'up' in c for c in calls)
    assert not (project/'docker-compose.override.yml').exists()
