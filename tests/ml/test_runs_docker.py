"""Opt-in tests run uploaded Python in real resource-limited containers."""

import base64
import json
import os
import subprocess
import time
import uuid

import pytest

from app.services.ml_controller import Controller
from app.services.ml_runs import Runs, atomic_json
from app.services.model_registry import Registry

pytestmark = pytest.mark.skipif(os.getenv('AUTOXAK_ML_DOCKER_INTEGRATION') != '1', reason='Requires built ML runtime image and Docker')


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv('ML_STORAGE_DIR', str(tmp_path/'store'))
    monkeypatch.setenv('ML_RUN_IMAGE', 'autoxak-ml-runtime:qa')
    volume = 'autoxak-ml-test-'+uuid.uuid4().hex
    monkeypatch.setenv('ML_STORAGE_VOLUME', volume)
    registry = Registry()
    subprocess.run(['docker','--host=unix:///var/run/docker.sock','volume','create','--driver','local',
                    '--opt','type=none','--opt','o=bind','--opt','device='+str(registry.root),volume],check=True,capture_output=True)
    controller = Controller()
    try:
        inspected = controller.docker(['image', 'inspect', controller.image, '--format', '{{.Id}}'])
        assert inspected.returncode == 0, 'Build autoxak-ml-runtime:qa before enabling Docker tests'
        controller.runtime_id = inspected.stdout.decode().strip()
        controller.heartbeat()
        yield controller
    finally:
        leftovers = controller.docker(['ps', '-aq', '--filter', 'label=autoxak.ml.volume='+volume])
        names = leftovers.stdout.decode().split()
        if names:
            controller.docker(['rm', '-f', *names])
        subprocess.run(['docker','--host=unix:///var/run/docker.sock','volume','rm',volume],capture_output=True)


def run(controller, code):
    model = Registry().import_bundle({'name':'Docker test', 'files':{'predict.py':base64.b64encode(code.encode()).decode()}})
    data = {'started_at_ms':1000,'ended_at_ms':61000,'trip_id':'fixture', 'gps':[],
            'control':{'same_interval':True,'distance_km':.6,'average_l_per_100km':8,'source':'obd'}}
    row = Runs().submit({'model_id':model['id'],'data':data})
    controller.execute(row['id'])
    return Runs().read(row['id'])


def test_real_python_sandbox_and_metrics(runtime):
    code = '''import os, socket
from pathlib import Path
def predict_trip(trip):
    assert "control" not in trip
    assert not any(key in os.environ for key in ("SECRET_KEY", "DATABASE_URL", "POSTGRES_PASSWORD"))
    assert not Path("/var/run/docker.sock").exists()
    assert not Path("/var/lib/autoxak/ml").exists()
    assert not Path("/input/original.json").exists()
    assert os.getuid() == 10001
    assert os.statvfs("/").f_flag & os.ST_RDONLY
    try:
        connection=socket.create_connection(("192.0.2.1",80),timeout=.1)
    except OSError: pass
    else: raise ValueError("Network should be unavailable")
    import numpy, sklearn, onnxruntime
    return {"estimated_fuel_l":.05,"interval_started_at_ms":trip["started_at_ms"],"interval_ended_at_ms":trip["ended_at_ms"]}
'''
    result = run(runtime, code)
    assert result['status'] == 'succeeded', result
    assert result['result']['trips'][0]['estimated_fuel_l'] == .05
    assert result['result']['mae_l'] == pytest.approx(.002)
    assert result['runtime_image_id'] == runtime.runtime_id
    assert result['result']['accuracy_certified'] is False
    assert Runs().read(result['id']) == result
    assert runtime.docker(['inspect','autoxak-ml-job-'+result['id']]).returncode != 0


@pytest.mark.parametrize('code,fragment', [
    ('def predict_trip(trip): raise ValueError("visible-model-error")', 'visible-model-error'),
    ('def predict_trip(trip): return {"estimated_fuel_l":-1}', 'неотрицательным'),
    ('import time\ndef predict_trip(trip): time.sleep(60)', 'превысило'),
    ('def predict_trip(trip): return {"estimated_fuel_l":.05,"oversized":"x"*(600*1024)}', '512 КиБ'),
    ('def predict_trip(trip): return {"allocation":bytearray(700*1024*1024)}', 'памяти'),
])
def test_model_failures_and_timeout_are_saved(runtime, code, fragment):
    runtime.timeout = 2 if "sleep(60)" in code else 30
    result = run(runtime, code)
    assert result['status'] == 'failed', result
    assert fragment in result['error']
    assert result['result'] is None
    assert Runs().runner()['ready']
    assert runtime.docker(['inspect','autoxak-ml-job-'+result['id']]).returncode != 0


def test_engine_applies_candidate_isolation(runtime):
    model = Registry().import_bundle({'name': 'Isolation inspection', 'files': {
        'predict.py': base64.b64encode(b'import time\ndef predict_trip(trip): time.sleep(60)').decode()}})
    row = Runs().submit({'model_id': model['id'], 'data': {'gps': []}})
    name = 'autoxak-ml-job-' + row['id']
    try:
        created = runtime.docker(runtime.command(row['id']))
        assert created.returncode == 0, created.stderr
        inspected = runtime.docker(['inspect', name])
        assert inspected.returncode == 0
        state = json.loads(inspected.stdout)[0]
        host = state['HostConfig']
        assert host['NetworkMode'] == 'none'
        assert host['ReadonlyRootfs'] is True
        assert host['Memory'] == host['MemorySwap'] == 512 * 1024 * 1024
        assert host['NanoCpus'] == 500000000
        assert host['PidsLimit'] == 64
        assert host['CapDrop'] == ['ALL']
        assert 'no-new-privileges' in host['SecurityOpt']
        assert state['Config']['User'] == '10001:10001'
        assert state['Image'] == runtime.runtime_id
        assert not host['Privileged']
        mounts = {mount['Destination']: mount for mount in state['Mounts']}
        # tmpfs mounts may be reported here by some Engine versions.
        assert set(mounts) <= {'/model', '/input', '/tmp', '/output'}
        assert {'/model', '/input'} <= set(mounts)
        for destination in ('/model', '/input'):
            assert mounts[destination]['RW'] is False
            assert mounts[destination]['Name'] == runtime.volume
        assert set(host['Tmpfs']) == {'/tmp', '/output'}
    finally:
        runtime.docker(['rm', '-f', name])
