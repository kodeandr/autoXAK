"""Anonymous test artifact storage; uploaded files are never loaded as models."""

from contextlib import contextmanager
from datetime import datetime, timezone
import base64
import binascii
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

MAX_BUNDLE_BYTES = 20 * 1024 * 1024
MAX_MODELS = 10
MAX_STORAGE_BYTES = 100 * 1024 * 1024
MAX_FILES = 16
FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}")


def enabled():
    return os.getenv("APP_ENV") == "test" and os.getenv("ML_UPLOAD_ENABLED", "false").lower() == "true"


def strict_json(payload):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Повторяющееся поле JSON")
            result[key] = value
        return result

    def invalid(_):
        raise ValueError("JSON содержит неконечное число")

    return json.loads(payload, object_pairs_hook=pairs, parse_constant=invalid)


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
            return {"models": {}}
        state = json.loads(path.read_text())
        # An older registry may contain an active pointer. It is deliberately
        # ignored: no uploaded artifact can enable retired fuel inference.
        return {"models": state["models"]}

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
        if not re.fullmatch(r"[a-f0-9]{32}", model_id):
            raise RegistryError("Комплект не найден", 404)
        return self.root / model_id

    def public_row(self, row):
        files = row.get("files")
        if files is None:
            # Preserve older uploads as research files, without importing their
            # libraries, interpreting weights or retaining activation controls.
            files = []
            for path in sorted(self.directory(row["id"]).iterdir()):
                if path.is_file() and FILENAME.fullmatch(path.name):
                    payload = path.read_bytes()
                    files.append({"name": path.name, "size_bytes": len(payload),
                                  "sha256": hashlib.sha256(payload).hexdigest()})
        return {"id": row["id"], "name": row["name"], "description": row.get("description", ""),
                "created_at": row["created_at"], "size_bytes": row["size_bytes"],
                "files": files, "status": "stored"}

    def listing(self):
        with self.lock():
            state = self.read()
            rows = sorted(state["models"].values(), key=lambda row: row["created_at"], reverse=True)
            return {"models": [self.public_row(row) for row in rows],
                    "inference_status": "model_not_trained", "max_bundle_bytes": MAX_BUNDLE_BYTES,
                    "max_models": MAX_MODELS, "max_files": MAX_FILES}

    def import_bundle(self, body):
        with self.lock("store", blocking=False):
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
        if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES:
            raise RegistryError("Выберите от 1 до 16 файлов")
        if any(not FILENAME.fullmatch(filename) for filename in files):
            raise RegistryError("Имена файлов: латинские буквы, цифры, точка, дефис и подчёркивание; до 120 символов")
        with self.lock():
            state = self.read()
            if len(state["models"]) >= MAX_MODELS:
                raise RegistryError("Достигнут лимит 10 комплектов. Удалите ненужные.", 409)
            stored = sum(row["size_bytes"] for row in state["models"].values())
        model_id = uuid.uuid4().hex
        temporary = Path(tempfile.mkdtemp(prefix=".upload-", dir=self.root))
        try:
            total, manifest = 0, []
            for filename, encoded in files.items():
                if not isinstance(encoded, str):
                    raise RegistryError("Некорректное содержимое файла")
                try:
                    payload = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError) as error:
                    raise RegistryError("Повреждённая загрузка файла") from error
                if not payload:
                    raise RegistryError("Пустые файлы не принимаются")
                total += len(payload)
                if total > MAX_BUNDLE_BYTES or stored + total > MAX_STORAGE_BYTES:
                    raise RegistryError("Превышен лимит размера загрузки или хранилища", 413)
                (temporary / filename).write_bytes(payload)
                manifest.append({"name": filename, "size_bytes": len(payload),
                                 "sha256": hashlib.sha256(payload).hexdigest()})
            row = {"id": model_id, "name": name.strip(), "description": description,
                   "created_at": datetime.now(timezone.utc).isoformat(), "size_bytes": total,
                   "files": sorted(manifest, key=lambda item: item["name"]), "status": "stored"}
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

    def delete(self, model_id):
        self.directory(model_id)
        with self.lock():
            state = self.read()
            if model_id not in state["models"]:
                raise RegistryError("Комплект не найден", 404)
            del state["models"][model_id]
            self.write(state)
            shutil.rmtree(self.directory(model_id))
