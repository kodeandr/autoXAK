"""Durable inference experiments, separate from recording and financial results."""

import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid
import zipfile

from app.services.model_registry import Registry, RegistryError, strict_json

MAX_INPUT_BYTES = 5 * 1024 * 1024
MAX_RUNS = 20
MAX_PENDING = 5
MAX_RUN_STORAGE = 150 * 1024 * 1024
INPUT_FIELDS = {"schema_version", "trip_id", "started_at_ms", "ended_at_ms", "gps", "motion",
                "vehicle", "device", "units", "features", "context"}


def atomic_json(path, value):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".state-")
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(value, output, allow_nan=False, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class Runs:
    def __init__(self):
        self.registry = Registry()
        self.root = self.registry.root / "runs"
        self.root.mkdir(exist_ok=True)

    def directory(self, run_id):
        self.registry.directory(run_id)  # UUID validation, no user-controlled paths.
        return self.root / run_id

    def read(self, run_id):
        path = self.directory(run_id) / "state.json"
        if not path.is_file():
            raise RegistryError("Проверка не найдена", 404)
        return strict_json(path.read_bytes())

    def rows(self):
        result = []
        for path in self.root.iterdir():
            if path.is_dir() and len(path.name) == 32:
                try:
                    result.append(self.read(path.name))
                except RegistryError:
                    pass
        return sorted(result, key=lambda item: item["created_at"], reverse=True)

    def runner(self):
        try:
            value = strict_json((self.registry.root / "runner.json").read_bytes())
            ready = time.time() - value["updated_at"] < 10 and value.get("ready") is True
            return {**value, "ready": ready}
        except (OSError, ValueError, KeyError, TypeError):
            return {"ready": False, "message": "Вычислитель не запущен"}

    def listing(self):
        with self.registry.lock("runs"):
            return {"runs": self.rows(), "runner": self.runner(), "max_input_bytes": MAX_INPUT_BYTES}

    def submit(self, body):
        if not isinstance(body, dict) or set(body) != {"model_id", "data"}:
            raise RegistryError("Нужны model_id и data с одной поездкой или списком поездок")
        self.registry.directory(body["model_id"])
        inputs = body["data"] if isinstance(body["data"], list) else [body["data"]]
        if not 1 <= len(inputs) <= 10 or not all(isinstance(row, dict) for row in inputs):
            raise RegistryError("Выберите от 1 до 10 объектов поездок")
        try:
            original = json.dumps(inputs, allow_nan=False, ensure_ascii=False, sort_keys=True).encode()
        except (ValueError, TypeError, RecursionError) as error:
            raise RegistryError("Некорректные тестовые данные") from error
        if len(original) > MAX_INPUT_BYTES:
            raise RegistryError("Тестовые данные превышают 5 МиБ", 413)
        if not self.runner()["ready"]:
            raise RegistryError("Вычислитель не запущен. Обновите тестовую среду командой test_ml.sh start.", 503)
        run_id = uuid.uuid4().hex
        directory = self.directory(run_id)
        with self.registry.lock("runs"), self.registry.lock():
            state = self.registry.read()
            model = state["models"].get(body["model_id"])
            if model is None:
                raise RegistryError("Модель не найдена", 404)
            source = self.registry.directory(body["model_id"])
            if not (source / "predict.py").is_file():
                raise RegistryError("В пакет нужен predict.py с функцией predict_trip(recording)")
            rows = self.rows()
            if len(rows) >= MAX_RUNS or sum(row["status"] in {"queued", "running"} for row in rows) >= MAX_PENDING:
                raise RegistryError("Достигнут лимит проверок. Дождитесь завершения или удалите старые.", 409)
            size = model["size_bytes"] + len(original) * 2
            if sum(row["storage_bytes"] for row in rows) + size > MAX_RUN_STORAGE:
                raise RegistryError("Хранилище проверок заполнено. Удалите старые результаты.", 413)
            directory.mkdir(mode=0o755)
            try:
                shutil.copytree(source, directory / "model")
                (directory / "model").chmod(0o755)
                files = []
                for path in sorted((directory / "model").iterdir()):
                    path.chmod(0o644)
                    files.append({"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
                (directory / "input").mkdir(mode=0o755)
                (directory / "input").chmod(0o755)
                # Ground truth and prior calculation results never enter the model container.
                sanitized = [{key: value for key, value in row.items() if key in INPUT_FIELDS} for row in inputs]
                atomic_json(directory / "input" / "input.json", sanitized)
                atomic_json(directory / "original.json", inputs)
                row = {"id": run_id, "model_id": model["id"], "model_name": model["name"],
                       "created_at": time.time(), "status": "queued", "storage_bytes": size,
                       "trip_count": len(inputs), "input_sha256": hashlib.sha256(original).hexdigest(),
                       "model_files": files, "result": None, "error": None}
                atomic_json(directory / "state.json", row)
            except Exception:
                shutil.rmtree(directory)
                raise
        return row

    def delete(self, run_id):
        with self.registry.lock("runs"):
            row = self.read(run_id)
            if row["status"] in {"queued", "running"}:
                raise RegistryError("Дождитесь завершения проверки", 409)
            shutil.rmtree(self.directory(run_id))


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def report(original, predictions):
    if not isinstance(predictions, list) or len(predictions) != len(original):
        raise ValueError("Вычислитель должен вернуть один результат на каждую поездку")
    rows, errors, controls = [], [], []
    for index, (trip, prediction) in enumerate(zip(original, predictions)):
        if not isinstance(prediction, dict):
            raise ValueError("predict_trip должен вернуть объект JSON")
        predicted = prediction.get("estimated_fuel_l")
        if predicted is not None and (not finite(predicted) or predicted < 0):
            raise ValueError("estimated_fuel_l должен быть конечным неотрицательным числом или null")
        control = trip.get("control")
        observed, reason = None, "Нет сопоставимого контрольного показания"
        if isinstance(control, dict) and control.get("same_interval") is True:
            distance, average = control.get("distance_km"), control.get("average_l_per_100km")
            start, end = trip.get("started_at_ms"), trip.get("ended_at_ms")
            matched = (finite(start) and finite(end) and end >= start
                       and prediction.get("interval_started_at_ms") == start
                       and prediction.get("interval_ended_at_ms") == end)
            if not matched:
                reason = "Прогноз и контроль относятся к разным или неуказанным интервалам"
            elif (finite(distance) and finite(average) and distance > 0 and 0 <= average <= 100
                  and control.get("source") in {"trip_computer", "obd", "fillup"}):
                observed = distance * average / 100
                reason = None
        error = predicted - observed if predicted is not None and observed is not None else None
        if error is not None:
            errors.append(abs(error))
            controls.append(observed)
        rows.append({"index": index, "trip_id": trip.get("trip_id"), "prediction": prediction,
                     "estimated_fuel_l": predicted, "observed_fuel_l": observed, "error_l": error,
                     "comparison_note": reason, "reference_source": control.get("source") if isinstance(control, dict) else None})
    per_source = {}
    for source in sorted({row["reference_source"] for row in rows if row["error_l"] is not None}):
        group = [row for row in rows if row["reference_source"] == source and row["error_l"] is not None]
        absolute = sum(abs(row["error_l"]) for row in group)
        total = sum(row["observed_fuel_l"] for row in group)
        per_source[source] = {"compared_trips": len(group), "mae_l": absolute / len(group),
                              "wape_percent": 100 * absolute / total if total > 0 else None}
    return {"trips": rows, "compared_trips": len(errors), "per_source": per_source,
            "mae_l": sum(errors) / len(errors) if errors else None,
            "wape_percent": 100 * sum(errors) / sum(controls) if controls and sum(controls) > 0 else None,
            "accuracy_certified": False, "reference_status": "caller_declared_not_independently_verified"}


def example_archive():
    output = io.BytesIO()
    # Synthetic interface example, deliberately not a trained fuel model.
    code = '''import json
from pathlib import Path

def predict_trip(recording):
    rate = json.loads(Path(__file__).with_name("weights.json").read_text())["synthetic_rate_lph"]
    start, end = recording["started_at_ms"], recording["ended_at_ms"]
    return {"status":"synthetic_example", "estimated_fuel_l":rate*(end-start)/3600000,
            "interval_started_at_ms":start, "interval_ended_at_ms":end,
            "quality_flags":["synthetic_interface_example_not_a_trained_model"]}
'''
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("predict.py", code)
        archive.writestr("weights.json", '{"synthetic_rate_lph":0.8}')
    return output.getvalue()
