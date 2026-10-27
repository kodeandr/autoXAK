"""The existing fuel-window contract, shared by inference and upload validation."""

import hashlib
import json
import math
from pathlib import Path

from app.ml.features import FEATURES, ROAD

FILES = frozenset({"catboost_direct.cbm", "catboost_residual.cbm", "model_card.json", "provenance.json"})
HASHED_FILES = FILES - {"provenance.json"}
MAX_BUNDLE_BYTES = 20 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024


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


def file_hash(path):
    payload = Path(path).read_bytes()
    if Path(path).suffix == ".json":
        payload = payload.replace(b"\r\n", b"\n")
    return hashlib.sha256(payload).hexdigest()


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def metadata(directory):
    directory = Path(directory)
    sizes = {name: (directory / name).stat().st_size for name in FILES}
    if not all(sizes.values()) or sum(sizes.values()) > MAX_BUNDLE_BYTES:
        raise ValueError("Комплект пуст или превышает 20 МиБ")
    if any(sizes[name] > MAX_JSON_BYTES for name in FILES if name.endswith(".json")):
        raise ValueError("JSON-файл превышает 1 МиБ")
    card = strict_json((directory / "model_card.json").read_bytes())
    provenance = strict_json((directory / "provenance.json").read_bytes())
    if not isinstance(card, dict) or not isinstance(provenance, dict):
        raise ValueError("Описание модели должно быть объектом JSON")
    version = provenance.get("model_version")
    if not isinstance(version, str) or not 1 <= len(version.strip()) <= 100:
        raise ValueError("Укажите model_version в provenance.json")
    selected = provenance.get("selected_model", "hybrid")
    if selected not in ("hybrid", "catboost", "physics"):
        raise ValueError("Неизвестный selected_model")
    hashes = provenance.get("artifact_sha256")
    if not isinstance(hashes, dict) or set(hashes) != HASHED_FILES:
        raise ValueError("artifact_sha256 должен содержать хеши двух CBM и model_card.json")
    if any(file_hash(directory / name) != hashes[name] for name in HASHED_FILES):
        raise ValueError("Контрольные суммы файлов не совпадают с provenance.json")
    if card.get("features") != FEATURES or card.get("hybrid_features") != FEATURES + ["physics"]:
        raise ValueError("Порядок признаков не совпадает с fuel-window-v1")
    if card.get("road_constants") != ROAD:
        raise ValueError("Физические константы несовместимы с приложением")
    coefficients = card.get("physics_coefficients_l_per_mj_l_per_h")
    if (not isinstance(coefficients, list) or len(coefficients) != 2
            or not all(finite(x) and x >= 0 for x in coefficients)):
        raise ValueError("Нужны два конечных неотрицательных физических коэффициента")
    ranges = card.get("input_ranges_train")
    if not isinstance(ranges, dict) or set(ranges) != set(FEATURES):
        raise ValueError("Нужны диапазоны обучения всех 17 признаков")
    for bounds in ranges.values():
        if (not isinstance(bounds, list) or len(bounds) != 2
                or not all(finite(x) for x in bounds) or bounds[0] > bounds[1]):
            raise ValueError("Некорректный диапазон обучения")
    identity = hashlib.sha256(json.dumps(
        {name: file_hash(directory / name) for name in sorted(FILES)}, sort_keys=True
    ).encode()).hexdigest()
    return card, {"model_version": version.strip(), "selected_model": selected,
                  "artifact_bundle_sha256": identity, "feature_schema_version": "fuel-window-v1",
                  "size_bytes": sum(sizes.values())}


def load_bundle(directory):
    from catboost import CatBoostRegressor

    card, info = metadata(directory)
    models = {}
    for name, filename, features in (
        ("catboost", "catboost_direct.cbm", FEATURES),
        ("hybrid", "catboost_residual.cbm", card["hybrid_features"]),
    ):
        model = CatBoostRegressor()
        model.load_model(str(Path(directory) / filename))
        if model.feature_names_ != features:
            raise ValueError("Признаки внутри CBM несовместимы с приложением")
        if model.tree_count_ > 2000:
            raise ValueError("Модель превышает лимит 2000 деревьев")
        models[name] = model
    return card, models, info
