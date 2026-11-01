"""No password reset, no credential exposure; exercise the complete recovery flow."""
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import pytest

spec=importlib.util.spec_from_file_location('auth_repair',Path('deploy/repair_legacy_db_auth.py'))
repair=importlib.util.module_from_spec(spec);spec.loader.exec_module(repair)


def test_compose_value_escapes_dollars_and_uri_password_is_separate():
    assert json.loads(repair.value_for_compose('x$y"z'))=='x$$y"z'


def test_recovery_uses_validated_stopped_container_credential_and_only_restarts_api(tmp_path,monkeypatch,capsys):
    original='# autoXAK HTTP recovery override\nservices:\n  api:\n    command: [uvicorn]\n'
    (tmp_path/'docker-compose.yml').write_text('services: {}')
    (tmp_path/'docker-compose.override.yml').write_text(original)
    def container(name):
        env=['POSTGRES_USER=autoXAK_user','POSTGRES_DB=autoXAK_db','POSTGRES_PASSWORD='+('new:p$ss@%' if name=='autoxak-production-postgres-1' else 'old')]
        if name=='autoXAK_backend_api':env+=['DATABASE_URL=postgresql+asyncpg://autoXAK_user:old@postgres:5432/autoXAK_db']
        return {'Name':'/'+name,'State':{'Running':name!='autoxak-production-postgres-1'},
                'Config':{'Env':env,'Labels':{'com.docker.compose.project':'autoxak','com.docker.compose.project.working_dir':str(tmp_path)}},
                'Mounts':[{'Name':'autoxak_pgdata','Destination':'/var/lib/postgresql/data'}],
                'NetworkSettings':{'Networks':{'backend':{'IPAddress':'172.20.0.2'}}}}
    monkeypatch.setattr(repair,'inspect',container)
    calls=[]
    def checked(args,**kwargs):
        calls.append(args);return SimpleNamespace(stdout='autoXAK_postgres\n' if args[:2]==['docker','ps'] else '')
    monkeypatch.setattr(repair,'checked',checked)
    monkeypatch.setattr(repair,'test_password',lambda *args:args[-1]=='new:p$ss@%')
    class Reply:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return b'{"status":"ready"}'
    monkeypatch.setattr(repair.urllib.request,'urlopen',lambda *args,**kwargs:Reply())
    repair.recover(tmp_path)
    revised=(tmp_path/'docker-compose.override.yml').read_text()
    assert 'new%3Ap%24ss%40%25' in revised and 'new:p$$ss@%' in revised
    output=capsys.readouterr().out
    assert 'new:p' not in output and 'new%3A' not in output
    assert any('--no-deps' in c and c[-1]=='api' for c in calls)
    assert all('ALTER' not in str(c) and 'down' not in c for c in calls)
    assert next(tmp_path.glob('backups/*/docker-compose.override.yml')).read_text()==original
    assert repair.update_override(revised,'next-url','next-password').count('environment:')==1


def test_custom_override_is_not_overwritten():
    with pytest.raises(repair.RecoveryError):repair.update_override('services: {}','url','secret')


def test_password_probe_passes_secret_only_on_stdin(monkeypatch):
    seen={}
    def run(args,**kwargs):seen.update(args=args,kwargs=kwargs);return SimpleNamespace(returncode=0,stdout='1\n')
    monkeypatch.setattr(repair.subprocess,'run',run)
    assert repair.test_password('pg','172.20.0.2','user','db','secret$p')
    assert 'secret$p' not in str(seen['args'])
    assert 'input' in seen['kwargs']
