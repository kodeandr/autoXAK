"""Anonymous file storage stays independent of any trained-model implementation."""

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.ml import router
from app.services.model_registry import Registry

HEADERS = {"X-Model-Manager": "1"}
PAYLOADS = {"future-model.bin": bytes(range(256)), "experiment.json": b'{"experiment":"new"}'}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ML_UPLOAD_ENABLED", "true")
    monkeypatch.setenv("ML_STORAGE_DIR", str(tmp_path / "models"))
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client


def bundle():
    return {"name": "Новый эксперимент", "description": "Алгоритм ещё не выбран", "files": {
        name: base64.b64encode(content).decode() for name, content in PAYLOADS.items()
    }}


def upload(client):
    response = client.post('/api/v3/ml/models', headers=HEADERS, json=bundle())
    assert response.status_code == 201, response.text
    return response.json()


def test_anonymous_upload_persistence_hashes_and_delete_without_inference(client):
    assert client.get('/ml').status_code == 200
    before = client.get('/api/v3/ml/models')
    assert before.headers['cache-control'] == 'no-store'
    assert before.json()['models'] == []
    assert before.json()['inference_status'] == 'model_not_trained'
    row = upload(client)
    assert row['status'] == 'stored'
    for file in row['files']:
        assert (Registry().directory(row['id']) / file['name']).read_bytes() == PAYLOADS[file['name']]
        assert file['sha256'] == hashlib.sha256(PAYLOADS[file['name']]).hexdigest()
    listing = Registry().listing()  # A new instance reads persistent state.
    assert listing['models'] == [row]
    assert listing['inference_status'] == 'model_not_trained'
    assert 'active_model_id' not in listing
    assert client.post(f'/api/v3/ml/models/{row["id"]}/activate', headers=HEADERS).status_code == 404
    assert client.delete(f'/api/v3/ml/models/{row["id"]}', headers=HEADERS).status_code == 204
    assert not Registry().directory(row['id']).exists()
    assert Registry().listing()['models'] == []


@pytest.mark.parametrize('environment,flag', [('production', 'true'), ('test', 'false'), ('', 'true')])
def test_feature_is_disabled_outside_explicit_test_environment(client, monkeypatch, environment, flag):
    monkeypatch.setenv('APP_ENV', environment)
    monkeypatch.setenv('ML_UPLOAD_ENABLED', flag)
    assert client.get('/ml').status_code == 404
    assert client.get('/api/v3/ml/models').status_code == 404
    assert client.post('/api/v3/ml/models', headers=HEADERS, json=bundle()).status_code == 404
    assert client.delete('/api/v3/ml/models/' + 'a'*32, headers=HEADERS).status_code == 404


@pytest.mark.parametrize('change', ['empty', 'many', 'traversal', 'absolute', 'base64', 'empty-file', 'name', 'description'])
def test_invalid_uploads_leave_no_partial_files(client, change):
    body = bundle()
    if change == 'empty': body['files'] = {}
    elif change == 'many': body['files'] = {f'{i}.bin': 'AQ==' for i in range(17)}
    elif change == 'traversal': body['files'] = {'../weights.bin': 'AQ=='}
    elif change == 'absolute': body['files'] = {'/tmp/weights.bin': 'AQ=='}
    elif change == 'base64': body['files']['future-model.bin'] = '%%%'
    elif change == 'empty-file': body['files']['future-model.bin'] = ''
    elif change == 'name': body['name'] = ''
    else: body['description'] = 'x'*2001
    assert client.post('/api/v3/ml/models', headers=HEADERS, json=body).status_code == 422
    assert Registry().listing()['models'] == []
    assert not list(Registry().root.glob('.upload-*'))


def test_cross_origin_mutations_are_blocked_without_requiring_an_account(client):
    assert client.post('/api/v3/ml/models', json=bundle()).status_code == 400
    assert client.post('/api/v3/ml/models', headers={**HEADERS, 'Origin': 'https://elsewhere.example'}, json=bundle()).status_code == 403
    assert client.post('/api/v3/ml/models', headers={**HEADERS, 'Sec-Fetch-Site': 'cross-site'}, json=bundle()).status_code == 403
    assert client.post('/api/v3/ml/models', headers={**HEADERS, 'Origin': 'http://testserver'}, json=bundle()).status_code == 201


def test_request_bundle_storage_and_count_limits(client, monkeypatch):
    import app.api.ml as api
    import app.services.model_registry as registry
    with monkeypatch.context() as patch:
        patch.setattr(api, 'MAX_REQUEST_BYTES', 10)
        assert client.post('/api/v3/ml/models', headers=HEADERS, json=bundle()).status_code == 413
    for constant, limit, status in [('MAX_BUNDLE_BYTES', 10, 413), ('MAX_STORAGE_BYTES', 10, 413), ('MAX_MODELS', 0, 409)]:
        with monkeypatch.context() as patch:
            patch.setattr(registry, constant, limit)
            assert client.post('/api/v3/ml/models', headers=HEADERS, json=bundle()).status_code == status
    assert Registry().listing()['models'] == []


def test_upload_lock_and_strict_transport_json(client):
    with Registry().lock('upload'):
        assert client.post('/api/v3/ml/models', headers=HEADERS, json=bundle()).status_code == 409
    for content in [b'{"name":"a","name":"b"}', b'{"name":NaN}', b'{broken']:
        assert client.post('/api/v3/ml/models', headers={**HEADERS, 'Content-Type': 'application/json'}, content=content).status_code == 422


def test_failed_registry_write_cleans_new_upload(client, monkeypatch):
    def fail(*args):
        raise OSError('Injected storage failure')
    monkeypatch.setattr(Registry, 'write', fail)
    with pytest.raises(OSError): upload(client)
    assert Registry().listing()['models'] == []
    assert not [p for p in Registry().root.iterdir() if p.is_dir()]


def test_older_registry_is_retained_as_files_and_active_pointer_is_ignored(client):
    registry = Registry()
    mid = 'b'*32
    directory = registry.directory(mid)
    directory.mkdir()
    (directory/'old-weights.cbm').write_bytes(b'historical bytes, never loaded')
    registry.write({'active': mid, 'models': {mid: {
        'id': mid, 'name': 'Исторические файлы', 'created_at': '2026-10-27T00:00:00Z',
        'size_bytes': (directory/'old-weights.cbm').stat().st_size,
        'model_version': 'retired', 'selected_model': 'hybrid',
    }}})
    listing = registry.listing()
    assert listing['inference_status'] == 'model_not_trained'
    assert 'active_model_id' not in listing
    assert listing['models'][0]['files'][0]['name'] == 'old-weights.cbm'
    assert listing['models'][0]['status'] == 'stored'
    assert client.delete(f'/api/v3/ml/models/{mid}', headers=HEADERS).status_code == 204
    assert not directory.exists()


def test_concurrent_deletions_do_not_corrupt_registry(client):
    ids = [upload(client)['id'] for _ in range(2)]
    with ThreadPoolExecutor(2) as executor:
        list(executor.map(lambda mid: Registry().delete(mid), ids))
    assert Registry().listing()['models'] == []


def test_unknown_and_unsafe_ids_are_not_deleted(client):
    assert client.delete('/api/v3/ml/models/' + 'a'*32, headers=HEADERS).status_code == 404
    assert client.delete('/api/v3/ml/models/not-a-safe-id', headers=HEADERS).status_code == 404
