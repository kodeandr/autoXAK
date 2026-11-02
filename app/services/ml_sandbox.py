"""Trusted entry point inside a disposable container; never import in the API."""

import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import traceback

MAX_OUTPUT = 512 * 1024


def read_result():
    try:
        descriptor = os.open("/output/result.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return 3
    with os.fdopen(descriptor, "rb") as file:
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
            return 4
        payload = file.read(MAX_OUTPUT + 1)
    if len(payload) > MAX_OUTPUT:
        return 4
    sys.stdout.buffer.write(payload)
    return 0


def main():
    if "--read-result" in sys.argv:
        return read_result()
    started = time.monotonic()
    try:
        requirements = Path("/model/requirements.txt")
        if requirements.exists():
            for line in requirements.read_text().splitlines():
                line = line.split("#")[0].strip()
                if not line:
                    continue
                match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)", line)
                if not match:
                    raise ValueError("requirements.txt: поддерживаются строки package==version")
                actual = importlib.metadata.version(match[1])
                if actual != match[2]:
                    raise ValueError(f"Версия {match[1]} в вычислителе: {actual}; в пакете: {match[2]}")
        sys.path.insert(0, "/model")
        specification = importlib.util.spec_from_file_location("candidate", "/model/predict.py")
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        if not callable(getattr(module, "predict_trip", None)):
            raise ValueError("В predict.py нужна функция predict_trip(recording)")
        inputs = json.loads(Path("/input/input.json").read_text())
        predictions = [module.predict_trip(row) for row in inputs]
        result = {"ok": True, "predictions": predictions, "runtime_seconds": time.monotonic() - started}
        payload = json.dumps(result, allow_nan=False).encode()
        if len(payload) > MAX_OUTPUT:
            raise ValueError("Результат превышает 512 КиБ")
    except BaseException as error:
        result = {"ok": False, "error": f"{type(error).__name__}: {str(error)[:1000]}",
                  "traceback": traceback.format_exc()[-4000:]}
        payload = json.dumps(result).encode()
    Path("/output/.result").write_bytes(payload)
    os.replace("/output/.result", "/output/result.json")
    # tmpfs disappears when the container stops; the controller reads it while alive.
    while True:
        time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
