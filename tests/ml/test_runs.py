import base64
import io
import json
import time
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.ml import router
from app.services.ml_runs import Runs, atomic_json, example_archive, report
from app.services.model_registry import Registry

HEADERS = {"X-Model-Manager": "1"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ML_UPLOAD_ENABLED", "true")
    monkeypatch.setenv("ML_STORAGE_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client


def upload(client, files=None):
    files = files or {"model.zip": example_archive()}
    response = client.post('/api/v3/ml/models', headers=HEADERS, json={
        'name': 'Candidate', 'files': {name: base64.b64encode(data).decode() for name, data in files.items()},
    })
    assert response.status_code == 201, response.text
    return response.json()


def ready():
    runs = Runs()
    atomic_json(runs.registry.root/'runner.json', {'ready': True, 'updated_at': time.time()})


def test_zip_is_unpacked_without_executing_and_marked_runnable(client):
    row = upload(client)
    assert row['runnable']
    assert {item['name'] for item in row['files']} == {'predict.py', 'weights.json'}
    assert client.get('/api/v3/ml/example-package').headers['content-type'] == 'application/zip'


@pytest.mark.parametrize('name', ['../predict.py', '/predict.py', 'folder/predict.py'])
def test_zip_paths_are_rejected(client, name):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr(name, 'def predict_trip(x): return {}')
    response = client.post('/api/v3/ml/models', headers=HEADERS, json={
        'name': 'Bad', 'files': {'bad.zip': base64.b64encode(buffer.getvalue()).decode()},
    })
    assert response.status_code == 422
    assert Registry().listing()['models'] == []


def test_runner_required_and_files_without_adapter_cannot_run(client):
    model = upload(client)
    data = client.get('/api/v3/ml/example-data').json()
    assert client.post('/api/v3/ml/runs', headers=HEADERS, json={'model_id': model['id'], 'data': data}).status_code == 503
    ready()
    plain = upload(client, {'weights.bin': b'weights'})
    assert client.post('/api/v3/ml/runs', headers=HEADERS, json={'model_id': plain['id'], 'data': data}).status_code == 422


def test_submit_freezes_inputs_and_model_and_excludes_ground_truth(client):
    model = upload(client)
    data = client.get('/api/v3/ml/example-data').json()
    data.update(fuel_estimate={'estimated_fuel_l': 123}, metrics={'secret': 'ground truth'})
    ready()
    response = client.post('/api/v3/ml/runs', headers=HEADERS, json={'model_id': model['id'], 'data': data})
    assert response.status_code == 202, response.text
    row = response.json()
    directory = Runs().directory(row['id'])
    sanitized = json.loads((directory/'input/input.json').read_text())[0]
    assert 'control' not in sanitized and 'fuel_estimate' not in sanitized and 'metrics' not in sanitized
    assert sanitized['gps'] == data['gps']
    assert json.loads((directory/'original.json').read_text())[0]['control'] == data['control']
    assert client.delete('/api/v3/ml/models/'+model['id'], headers=HEADERS).status_code == 204
    assert (directory/'model/predict.py').is_file()
    assert client.delete('/api/v3/ml/runs/'+row['id'], headers=HEADERS).status_code == 409
    row.update(status='succeeded')
    atomic_json(directory/'state.json', row)
    assert client.get('/api/v3/ml/runs/'+row['id']).json()['status'] == 'succeeded'
    assert client.delete('/api/v3/ml/runs/'+row['id'], headers=HEADERS).status_code == 204


@pytest.mark.parametrize('body', [{}, {'model_id': [], 'data': {}}, {'model_id': 'x', 'data': {}},
                                  {'model_id':'a'*32, 'data': []}, {'model_id':'a'*32, 'data': [None]}])
def test_invalid_requests_do_not_create_runs(client, body):
    ready()
    assert client.post('/api/v3/ml/runs', headers=HEADERS, json=body).status_code in {404, 422}
    assert Runs().listing()['runs'] == []


def test_runs_are_disabled_in_production(client, monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    assert client.get('/api/v3/ml/runs').status_code == 404
    assert client.post('/api/v3/ml/runs', headers=HEADERS, json={}).status_code == 404


def test_run_limits_and_stale_heartbeat(client, monkeypatch):
    import app.services.ml_runs as service
    model = upload(client)
    data = client.get('/api/v3/ml/example-data').json()
    ready()
    for constant, value, status in [('MAX_RUNS',0,409), ('MAX_PENDING',0,409), ('MAX_RUN_STORAGE',1,413), ('MAX_INPUT_BYTES',1,413)]:
        with monkeypatch.context() as patch:
            patch.setattr(service, constant, value)
            assert client.post('/api/v3/ml/runs', headers=HEADERS, json={'model_id':model['id'],'data':data}).status_code == status
    atomic_json(Runs().registry.root/'runner.json', {'ready':True,'updated_at':time.time()-60})
    assert not Runs().runner()['ready']


def test_metrics_match_intervals_and_do_not_certify_accuracy():
    trip = {'trip_id':'a','started_at_ms':1,'ended_at_ms':2,
            'control': {'same_interval':True,'distance_km':10,'average_l_per_100km':8,'source':'obd'}}
    prediction = {'estimated_fuel_l':.9, 'interval_started_at_ms':1, 'interval_ended_at_ms':2}
    result = report([trip], [prediction])
    assert result['compared_trips'] == 1
    assert result['mae_l'] == pytest.approx(.1)
    assert result['wape_percent'] == pytest.approx(12.5)
    assert not result['accuracy_certified']
    assert report([trip], [{'estimated_fuel_l':.9}])['compared_trips'] == 0
    trip['control']['same_interval'] = False
    assert report([trip],[prediction])['wape_percent'] is None


@pytest.mark.parametrize('number', [float('nan'), float('inf'), -1, True])
def test_bad_predictions_fail_instead_of_reporting_zero(number):
    with pytest.raises(ValueError): report([{}], [{'estimated_fuel_l': number}])
