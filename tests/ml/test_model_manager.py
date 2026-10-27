"""Model management is DB-independent; tests also run with --confcutdir=tests/ml."""

import base64
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.ml import router
from app.ml.bundle import FILES
from app.services import fuel_prediction
from app.services.model_registry import BUILTIN, Registry, RegistryError

HEADERS = {"X-Model-Manager": "1"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ML_UPLOAD_ENABLED", "true")
    monkeypatch.setenv("ML_STORAGE_DIR", str(tmp_path / "models"))
    clear_cache = fuel_prediction._load_artifacts.cache_clear
    clear_cache()
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client
    clear_cache()


def bundle(version="candidate-v2", selected="hybrid"):
    files = {name: (BUILTIN / name).read_bytes() for name in FILES}
    provenance = json.loads(files["provenance.json"])
    provenance.update(model_version=version, selected_model=selected)
    files["provenance.json"] = json.dumps(provenance).encode()
    return {"name": "Новая модель", "description": "Проверка", "files": {
        name: base64.b64encode(content).decode() for name, content in files.items()
    }}


def upload(client, **kwargs):
    response = client.post("/api/v3/ml/models", headers=HEADERS, json=bundle(**kwargs))
    assert response.status_code == 201, response.text
    return response.json()


def trip():
    start = 1_000_000
    gps = [{"timestamp_ms": start + i * 1000, "speed_mps": 10., "accuracy_m": 5., "speed_source": "gps"}
           for i in range(61)]
    profile = {"fuel_type": "gasoline", "mass_kg": 1500., "displacement_l": 2., "turbo": True}
    return gps, profile, start, start + 60_000


def test_anonymous_lifecycle_persistence_and_actual_inference(client):
    # No Authorization header, account, password or database fixture.
    assert client.get("/ml").status_code == 200
    initial = client.get("/api/v3/ml/models")
    assert initial.headers["cache-control"] == "no-store"
    assert initial.json()["active_model_id"] == "builtin"
    baseline = fuel_prediction.predict_fuel(*trip())
    assert baseline["status"] == "research_estimate"
    candidate = upload(client, selected="physics")
    assert Registry().listing()["active_model_id"] == "builtin"
    assert Registry().listing()["models"][1]["model_version"] == "candidate-v2"
    mid = candidate["id"]
    assert client.post(f"/api/v3/ml/models/{mid}/activate", headers=HEADERS).status_code == 200
    # New registry object + empty inference cache simulates API process recreation.
    fuel_prediction._load_artifacts.cache_clear()
    assert Registry().listing()["active_model_id"] == mid
    prediction = fuel_prediction.predict_fuel(*trip())
    assert prediction["status"] == "research_estimate"
    assert prediction["model_version"] == "candidate-v2"
    assert prediction["model_bundle_id"] == mid
    assert prediction["artifact_bundle_sha256"] == candidate["artifact_bundle_sha256"]
    assert prediction["estimated_fuel_l"] == prediction["fuel_l_on_retained_intervals"]["physics"]
    assert baseline["model_version"] != prediction["model_version"]
    assert client.delete(f"/api/v3/ml/models/{mid}", headers=HEADERS).status_code == 409
    assert client.post("/api/v3/ml/models/builtin/activate", headers=HEADERS).status_code == 200
    assert client.delete(f"/api/v3/ml/models/{mid}", headers=HEADERS).status_code == 204
    assert not Registry().directory(mid).exists()
    assert Registry().listing()["active_model_id"] == "builtin"
    restored = fuel_prediction.predict_fuel(*trip())
    assert restored["estimated_fuel_l"] == pytest.approx(baseline["estimated_fuel_l"])
    assert client.delete("/api/v3/ml/models/builtin", headers=HEADERS).status_code == 409


@pytest.mark.parametrize("environment,flag", [("production", "true"), ("test", "false"), ("", "true")])
def test_disabled_in_production_and_by_default(client, monkeypatch, environment, flag):
    monkeypatch.setenv("APP_ENV", environment)
    monkeypatch.setenv("ML_UPLOAD_ENABLED", flag)
    assert client.get("/ml").status_code == 404
    assert client.get("/api/v3/ml/models").status_code == 404
    assert client.post("/api/v3/ml/models", headers=HEADERS, json={}).status_code == 404
    assert client.delete("/api/v3/ml/models/builtin", headers=HEADERS).status_code == 404
    assert client.post("/api/v3/ml/models/builtin/activate", headers=HEADERS).status_code == 404
    assert fuel_prediction.predict_fuel(*trip())["model_bundle_id"] == "builtin"


def test_cross_origin_is_not_an_account_requirement(client):
    assert client.post("/api/v3/ml/models", json={}).status_code == 400
    for headers in ({**HEADERS, "Origin": "https://elsewhere.example"},
                    {**HEADERS, "Sec-Fetch-Site": "cross-site"}):
        assert client.post("/api/v3/ml/models", headers=headers, json=bundle()).status_code == 403
    assert client.post("/api/v3/ml/models", headers={**HEADERS, "Origin": "http://testserver"}, json=bundle()).status_code == 201


@pytest.mark.parametrize("change", ["missing", "traversal", "hash", "base64", "features", "ranges", "coefficients"])
def test_rejected_upload_never_changes_active_model(client, change):
    body = bundle()
    if change == "missing":
        del body["files"]["model_card.json"]
    elif change == "traversal":
        body["files"]["../provenance.json"] = body["files"].pop("provenance.json")
    elif change == "base64":
        body["files"]["model_card.json"] = "%%%"
    elif change == "hash":
        body["files"]["catboost_direct.cbm"] = base64.b64encode(b"bad weights").decode()
    else:
        card = json.loads(base64.b64decode(body["files"]["model_card.json"]))
        if change == "features":
            card["features"].reverse()
        elif change == "ranges":
            card["input_ranges_train"]["duration_s"] = [30, 5]
        else:
            card["physics_coefficients_l_per_mj_l_per_h"] = [-1, 1]
        encoded = json.dumps(card).encode()
        body["files"]["model_card.json"] = base64.b64encode(encoded).decode()
        provenance = json.loads(base64.b64decode(body["files"]["provenance.json"]))
        import hashlib
        provenance["artifact_sha256"]["model_card.json"] = hashlib.sha256(encoded).hexdigest()
        body["files"]["provenance.json"] = base64.b64encode(json.dumps(provenance).encode()).decode()
    response = client.post("/api/v3/ml/models", headers=HEADERS, json=body)
    assert response.status_code == 422, response.text
    assert Registry().listing()["active_model_id"] == "builtin"
    assert len(Registry().listing()["models"]) == 1
    assert not list(Registry().root.glob(".upload-*"))


def test_native_parser_failure_and_timeout_cleanup(client, monkeypatch):
    for result in (subprocess.CompletedProcess([], -9), subprocess.TimeoutExpired([], 30)):
        def failed(*args, **kwargs):
            # Credentials and database environment never go to the native validator.
            assert "POSTGRES_PASSWORD" not in kwargs["env"]
            assert "SECRET_KEY" not in kwargs["env"]
            if isinstance(result, Exception):
                raise result
            return result
        monkeypatch.setattr(subprocess, "run", failed)
        response = client.post("/api/v3/ml/models", headers=HEADERS, json=bundle())
        assert response.status_code == 422
        assert len(Registry().listing()["models"]) == 1
        assert not list(Registry().root.glob(".upload-*"))


def test_activation_failure_preserves_previous_selection(client, monkeypatch):
    candidate = upload(client)
    def broken(*args):
        raise ValueError("Native failure")
    monkeypatch.setattr(fuel_prediction, "_load_artifacts", broken)
    response = client.post(f'/api/v3/ml/models/{candidate["id"]}/activate', headers=HEADERS)
    assert response.status_code == 422
    assert Registry().listing()["active_model_id"] == "builtin"


def test_global_upload_lock_and_request_size(client, monkeypatch):
    import app.api.ml as api
    with Registry().lock("upload"):
        response = client.post("/api/v3/ml/models", headers=HEADERS, json=bundle())
        assert response.status_code == 409
    monkeypatch.setattr(api, "MAX_REQUEST_BYTES", 10)
    assert client.post("/api/v3/ml/models", headers=HEADERS, json=bundle()).status_code == 413


def test_storage_quota_and_version_limit(client, monkeypatch):
    import app.services.model_registry as registry
    monkeypatch.setattr(registry, "MAX_STORAGE_BYTES", 10)
    assert client.post("/api/v3/ml/models", headers=HEADERS, json=bundle()).status_code == 413
    monkeypatch.setattr(registry, "MAX_MODELS", 0)
    assert client.post("/api/v3/ml/models", headers=HEADERS, json=bundle()).status_code == 409


def test_concurrent_activation_and_delete_keep_registry_consistent(client):
    candidate = upload(client)
    mid = candidate["id"]
    def activate():
        try:
            Registry().activate(mid)
        except RegistryError as error:
            assert error.status == 404
    def delete():
        try:
            Registry().delete(mid)
        except RegistryError as error:
            assert error.status == 409
    with ThreadPoolExecutor(2) as executor:
        list(executor.map(lambda fn: fn(), [activate, delete]))
    state = Registry().listing()
    ids = {row["id"] for row in state["models"]}
    assert state["active_model_id"] in ids
    assert (mid in ids) == Registry().directory(mid).exists()


def test_inference_failure_returns_unavailable_not_zero(client, monkeypatch):
    import app.services.model_registry as registry
    def broken(*args):
        raise ValueError("Model unavailable")
    monkeypatch.setattr(registry, "active_artifacts", broken)
    result = fuel_prediction.predict_fuel(*trip())
    assert result["status"] == "model_unavailable"
    assert result["estimated_fuel_l"] is None


def test_crlf_normalization_and_strict_json(client):
    body = bundle()
    card = base64.b64decode(body["files"]["model_card.json"]).replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    body["files"]["model_card.json"] = base64.b64encode(card).decode()
    assert client.post("/api/v3/ml/models", headers=HEADERS, json=body).status_code == 201
    duplicate = b'{"name":"a","name":"b","files":{}}'
    assert client.post("/api/v3/ml/models", headers={**HEADERS, "Content-Type": "application/json"}, content=duplicate).status_code == 422
