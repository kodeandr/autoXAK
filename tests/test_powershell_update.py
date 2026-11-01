"""Exercise PowerShell orchestration with a deterministic Docker CLI substitute."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

PWSH = os.environ.get('AUTOXAK_PWSH') or shutil.which('pwsh')
pytestmark = pytest.mark.skipif(not PWSH, reason='PowerShell is not installed')


def invoke(tmp_path, mode='Rebuild', failure=''):
    root = tmp_path / 'repo'
    (root / 'deploy').mkdir(parents=True)
    shutil.copyfile('deploy/update.ps1', root / 'deploy/update.ps1')
    for directory in ('app', 'static'):
        (root / directory).mkdir()
        (root / directory / 'example.txt').write_text('complete file')
    for name in ('compose.production.yml', '.env.production', 'deploy/entrypoint.py', 'seed_fleet.py', 'seed_fuel_prices.py'):
        (root / name).write_text('test fixture')
    command = tmp_path / 'docker'
    command.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['DOCKER_CALLS'],'a') as out: out.write(json.dumps(args)+'\\n')
if args[0]=='inspect':
 print(('true' if os.environ.get('FAILURE')=='readonly' else 'false') if 'ReadonlyRootfs' in args[2] else '' if 'Config.User' in args[2] else 'sha256:previous-image')
elif args[0]=='compose' and 'ps' in args: print('test-api' if args[-1]=='api' else 'test-postgres')
elif args[0]=='cp' and args[1].startswith('test-'):
 target=Path(args[2]);target.parent.mkdir(parents=True,exist_ok=True)
 if args[1].endswith('/app') or args[1].endswith('/static'): target.mkdir(exist_ok=True)
 else: target.write_bytes(b'PGDMP-validated-dump')
elif args[0]=='exec' and 'python' in args and os.environ.get('FAILURE')=='health': sys.exit(1)
elif args[0]=='compose' and 'build' in args and os.environ.get('FAILURE')=='build': sys.exit(9)
''')
    command.chmod(0o755)
    log = tmp_path / 'calls.jsonl'
    env = dict(os.environ, PATH=str(tmp_path)+os.pathsep+os.environ['PATH'],
               DOCKER_CALLS=str(log), FAILURE=failure,
               XDG_CACHE_HOME=str(tmp_path/'cache'), XDG_CONFIG_HOME=str(tmp_path/'config'), XDG_DATA_HOME=str(tmp_path/'data'))
    reply = subprocess.run([PWSH,'-NoProfile','-File',str(root/'deploy/update.ps1'),'-Mode',mode,'-HealthTimeout','5'],env=env,capture_output=True,text=True,timeout=30)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    return reply, calls, root


def test_powershell_rebuild_backs_up_binary_dump_before_restarting(tmp_path):
    reply,calls,root=invoke(tmp_path)
    assert reply.returncode==0,reply.stdout+reply.stderr
    dump=next(root.glob('backups/*/database.dump'))
    assert dump.read_bytes()==b'PGDMP-validated-dump'
    backup=next(i for i,c in enumerate(calls) if c[0]=='cp' and c[1].endswith('.dump'))
    build=next(i for i,c in enumerate(calls) if 'build' in c)
    up=next(i for i,c in enumerate(calls) if 'up' in c)
    assert backup<build<up
    assert not any('down' in c for c in calls)


def test_powershell_copy_refuses_read_only_production_before_mutation(tmp_path):
    reply,calls,root=invoke(tmp_path,'Copy','readonly')
    assert reply.returncode!=0
    assert 'read-only' in reply.stderr
    assert not any(c[0]=='cp' or 'stop' in c or 'build' in c for c in calls)


def test_powershell_copy_includes_new_modules_and_restarts(tmp_path):
    reply,calls,_=invoke(tmp_path,'Copy')
    assert reply.returncode==0,reply.stdout+reply.stderr
    copies=[c for c in calls if c[0]=='cp' and c[-1].startswith('test-api:')]
    assert {c[-1] for c in copies}=={'test-api:/app/'+p for p in ('app','static','deploy/entrypoint.py','seed_fleet.py','seed_fuel_prices.py')}
    stop=next(i for i,c in enumerate(calls) if 'stop' in c)
    first_copy=next(i for i,c in enumerate(calls) if c in copies)
    start=next(i for i,c in enumerate(calls) if 'start' in c)
    assert stop<first_copy<start


def test_powershell_failed_readiness_restores_image_without_restoring_database(tmp_path):
    reply,calls,root=invoke(tmp_path,failure='health')
    assert reply.returncode!=0
    rollback=next(root.glob('backups/*/rollback.json'))
    assert json.loads(rollback.read_text())['services']['api']['image']=='sha256:previous-image'
    assert any('--no-build' in c for c in calls)
    assert not any('pg_restore' in c for c in calls if c[0]=='exec' and len(c)>2 and c[2]!='sh')
