"""Persistent, atomic model registry for the explicitly enabled test environment."""

from contextlib import contextmanager
from datetime import datetime, timezone
import base64
import binascii
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

from app.ml.bundle import FILES, MAX_BUNDLE_BYTES, metadata

MAX_MODELS = 10
MAX_STORAGE_BYTES = 100 * 1024 * 1024
BUILTIN = Path(__file__).resolve().parents[1] / "ml" / "artifacts"


def enabled():
    return os.getenv("APP_ENV") == "test" and os.getenv("ML_UPLOAD_ENABLED", "false").lower() == "true"


class RegistryError(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


class Registry:
    def __init__(self):
        self.root = Path(os.getenv("ML_STORAGE_DIR", "/var/lib/autoxak/ml"))
        self.root.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def lock(self, name="registry", blocking=True):
        with (self.root / (name + ".lock")).open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError as error:
                raise RegistryError("Другая загрузка уже выполняется. Повторите позже.", 409) from error
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def read(self):
        path = self.root / "registry.json"
        if not path.exists():
            return {"active": "builtin", "models": {}}
        return json.loads(path.read_text())

    def write(self, state):
        fd, temporary = tempfile.mkstemp(dir=self.root, prefix=".registry-")
        try:
            with os.fdopen(fd, "w") as output:
                json.dump(state, output, ensure_ascii=False, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.root / "registry.json")
        finally:
            Path(temporary).unlink(missing_ok=True)

    def directory(self, model_id):
        if model_id == "builtin":
            return BUILTIN
        if not re.fullmatch(r"[a-f0-9]{32}", model_id):
            raise RegistryError("Модель не найдена", 404)
        return self.root / model_id

    def listing(self):
        with self.lock():
            state = self.read()
            _, info = metadata(BUILTIN)
            builtin = {"id": "builtin", "name": "Встроенная модель", "description": "Исходный комплект приложения",
                       "created_at": None, "builtin": True, **info}
            rows = [builtin] + sorted(state["models"].values(), key=lambda row: row["created_at"], reverse=True)
            return {"active_model_id": state["active"], "models": rows,
                    "max_bundle_bytes": MAX_BUNDLE_BYTES, "max_models": MAX_MODELS}

    def import_bundle(self, body):
        with self.lock("validation", blocking=False):
            return self._import_bundle(body)

    def _import_bundle(self, body):
        if not isinstance(body, dict) or set(body) - {"name", "description", "files"}:
            raise RegistryError("Некорректное описание загрузки")
        name, description = body.get("name"), body.get("description", "")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise RegistryError("Название: от 1 до 100 символов")
        if not isinstance(description, str) or len(description) > 2000:
            raise RegistryError("Описание: не более 2000 символов")
        files = body.get("files")
        if not isinstance(files, dict) or set(files) != FILES:
            raise RegistryError("Выберите два CBM, model_card.json и provenance.json")
        with self.lock():
            state = self.read()
            if len(state["models"]) >= MAX_MODELS:
                raise RegistryError("Достигнут лимит 10 версий. Удалите ненужные.", 409)
            stored = sum(row["size_bytes"] for row in state["models"].values())
        model_id = uuid.uuid4().hex
        temporary = Path(tempfile.mkdtemp(prefix=".upload-", dir=self.root))
        try:
            total = 0
            for filename, encoded in files.items():
                if not isinstance(encoded, str):
                    raise RegistryError("Некорректное содержимое файла")
                try:
                    payload = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError) as error:
                    raise RegistryError("Повреждённая загрузка файла") from error
                total += len(payload)
                if total > MAX_BUNDLE_BYTES or stored + total > MAX_STORAGE_BYTES:
                    raise RegistryError("Превышен лимит размера загрузки или хранилища", 413)
                (temporary / filename).write_bytes(payload)
            try:
                _, info = metadata(temporary)
            except (ValueError, KeyError, TypeError, OSError, RecursionError) as error:
                raise RegistryError(str(error)) from error
            worker = Path(__file__).resolve().parents[1] / "ml" / "validate_bundle.py"
            environment = {"PATH": os.defpath, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
                           "MKL_NUM_THREADS": "1", "PYTHONDONTWRITEBYTECODE": "1"}
            try:
                check = subprocess.run([sys.executable, "-I", "-B", str(worker), str(temporary)],
                                       env=environment, cwd=temporary, capture_output=True, timeout=30)
            except subprocess.TimeoutExpired as error:
                raise RegistryError("Проверка модели превысила 30 секунд") from error
            if check.returncode != 0:
                raise RegistryError("CBM не прошёл проверку загрузки, признаков, памяти или пробного расчёта")
            row = {"id": model_id, "name": name.strip(), "description": description,
                   "created_at": datetime.now(timezone.utc).isoformat(), "builtin": False,
                   "validation": {"probe_rows": 3, "finite_predictions": True}, **info}
            with self.lock():
                state = self.read()
                os.replace(temporary, self.directory(model_id))
                state["models"][model_id] = row
                try:
                    self.write(state)
                except Exception:
                    shutil.rmtree(self.directory(model_id))
                    raise
            return row
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def activate(self, model_id):
        from app.services.fuel_prediction import _load_artifacts

        with self.lock():
            state = self.read()
            if model_id != "builtin" and model_id not in state["models"]:
                raise RegistryError("Модель не найдена", 404)
            try:
                _load_artifacts(str(self.directory(model_id)))
            except Exception as error:
                raise RegistryError("Модель не удалось загрузить. Прежняя версия сохранена.") from error
            state["active"] = model_id
            self.write(state)
        return {"active_model_id": model_id}

    def delete(self, model_id):
        self.directory(model_id)
        with self.lock():
            state = self.read()
            if model_id == "builtin":
                raise RegistryError("Встроенная модель нужна для возврата и не удаляется", 409)
            if model_id not in state["models"]:
                raise RegistryError("Модель не найдена", 404)
            if state["active"] == model_id:
                raise RegistryError("Сначала примените другую модель или вернитесь к встроенной", 409)
            # Persist deletion first: new requests cannot select files about to be removed.
            del state["models"][model_id]
            self.write(state)
            shutil.rmtree(self.directory(model_id))


def active_artifacts(loader):
    # Hold the same lock as delete until native models are fully loaded into memory.
    registry = Registry()
    with registry.lock():
        state = registry.read()
        model_id = state["active"]
        if model_id != "builtin" and model_id not in state["models"]:
            raise ValueError("Active model is missing")
        card, models, info = loader(str(registry.directory(model_id)))
        return card, models, {**info, "model_bundle_id": model_id}
