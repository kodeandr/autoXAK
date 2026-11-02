"""Read-only readiness by default; explicit synthetic inference on test/loopback only.

Standalone stdlib script: also runs via stdin inside the existing API container.
Only allowlisted metadata and this script's own run identifiers go to stdout.
"""

import argparse
import base64
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class CheckError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CheckError("Unexpected redirect; check the test URL")


def validate_url(value):
    url = urllib.parse.urlsplit(value)
    if (url.username or url.password or url.query or url.fragment or url.path not in ("", "/")
            or not (value.rstrip("/") == "https://test.autoxak.ru"
                    or url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"})):
        raise CheckError("Use https://test.autoxak.ru or an HTTP loopback origin without credentials")
    try:
        url.port
    except ValueError as error:
        raise CheckError("Invalid port") from error
    return value.rstrip("/")


class Client:
    def __init__(self, base_url):
        self.base_url = validate_url(base_url)
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, path, body=None, expected=200):
        data = None if body is None else json.dumps(body, allow_nan=False).encode()
        headers = {"Accept": "application/json"}
        if data is not None:
            headers.update({"Content-Type": "application/json", "X-Model-Manager": "1"})
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers)
        try:
            with self.opener.open(request, timeout=8) as response:
                if response.status != expected:
                    raise CheckError(f"Unexpected HTTP status for {path}")
                payload = response.read(16 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as error:
            # Do not echo response bodies: they can contain unrelated data or logs.
            raise CheckError(f"HTTP {error.code} for {path}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise CheckError(f"Request failed for {path}") from error
        if len(payload) > 16 * 1024 * 1024:
            raise CheckError("Response exceeds diagnostic limit")
        try:
            return json.loads(payload)
        except (ValueError, UnicodeError) as error:
            raise CheckError(f"Invalid JSON for {path}") from error


def readiness(client):
    client.request("/ready")
    listing = client.request("/api/v3/ml/runs")
    runner = listing.get("runner", {}) if isinstance(listing, dict) else {}
    if not isinstance(runner, dict):
        raise CheckError("Invalid runner status")
    stamp = runner.get("updated_at")
    fresh = (isinstance(stamp, (int, float)) and not isinstance(stamp, bool)
             and math.isfinite(stamp) and -2 <= time.time() - stamp < 10)
    image_id = runner.get("image_id")
    if runner.get("ready") is not True or not fresh:
        raise CheckError("Model runner is unavailable or its heartbeat is stale")
    if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise CheckError("Model runner has no immutable runtime image ID")
    rows = listing.get("runs")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise CheckError("Invalid run listing")
    return {"status": "ready", "runtime_image_id": image_id}, rows


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise CheckError("Invalid identifier returned by test API")
    return value


def smoke(client, summary, rows, timeout=90):
    if any(row.get("status") in {"queued", "running"} for row in rows):
        raise CheckError("Wait for existing test runs to finish before synthetic smoke")
    code = '''def predict_trip(trip):
    assert "control" not in trip
    return {"status":"synthetic_smoke", "estimated_fuel_l":0.05,
            "interval_started_at_ms":trip["started_at_ms"],
            "interval_ended_at_ms":trip["ended_at_ms"],
            "quality_flags":["synthetic_not_a_trained_model"]}
'''
    uploaded = client.request("/api/v3/ml/models", {
        "name": "Synthetic smoke " + uuid.uuid4().hex[:8],
        "files": {"predict.py": base64.b64encode(code.encode()).decode()},
    }, expected=201)
    summary["model_id"] = identifier(uploaded.get("id"))
    trip = {"schema_version": 2, "trip_id": "synthetic-smoke-no-personal-data",
            "started_at_ms": 1000000, "ended_at_ms": 1060000,
            "gps": [], "control": {"same_interval": True, "source": "obd",
                                      "distance_km": .6, "average_l_per_100km": 8}}
    queued = client.request("/api/v3/ml/runs", {"model_id": summary["model_id"], "data": trip}, expected=202)
    summary["run_id"] = identifier(queued.get("id"))
    if queued.get("status") != "queued":
        raise CheckError("Test API did not acknowledge the queue")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = client.request("/api/v3/ml/runs/" + summary["run_id"])
        status = row.get("status")
        if status == "failed":
            raise CheckError("Synthetic model failed; inspect this run in /ml")
        if status == "succeeded":
            result = row.get("result") or {}
            trips = result.get("trips", [])
            valid = (len(trips) == 1 and trips[0].get("estimated_fuel_l") == .05
                     and result.get("compared_trips") == 1
                     and result.get("accuracy_certified") is False
                     and isinstance(result.get("mae_l"), (int, float))
                     and math.isclose(result["mae_l"], .002, abs_tol=1e-12)
                     and row.get("runtime_image_id") == summary["runtime_image_id"]
                     and row.get("model_id") == summary["model_id"])
            if not valid:
                raise CheckError("Synthetic result or runtime identity does not match the fixture")
            summary.update(status="succeeded", synthetic=True, accuracy_certified=False,
                           estimated_fuel_l=.05, mae_l=result["mae_l"])
            return
        if status not in {"queued", "running"}:
            raise CheckError("Unexpected test run status")
        time.sleep(.5)
    raise CheckError("Timed out waiting for this run; it remains visible in /ml")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://test.autoxak.ru")
    parser.add_argument("--run-synthetic", action="store_true",
                        help="Create one model and run; retain both for inspection in /ml")
    args = parser.parse_args(argv)
    summary = {}
    try:
        client = Client(args.base_url)
        ready, rows = readiness(client)
        summary.update(ready)
        if args.run_synthetic:
            smoke(client, summary, rows)
    except (CheckError, ValueError, TypeError, KeyError, AttributeError) as error:
        # Unexpected response shapes are failures, never fabricated success.
        summary.update(status="failed", error=str(error) if isinstance(error, CheckError) else "Invalid API response")
        print(json.dumps(summary, ensure_ascii=True, allow_nan=False))
        return 1
    print(json.dumps(summary, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
