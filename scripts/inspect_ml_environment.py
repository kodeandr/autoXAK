"""Read-only, allowlisted version report. Never dump env, logs or trip results."""

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import subprocess
import time
import urllib.parse
import urllib.request


MAX_RESPONSE_BYTES = 1024 * 1024


def command(arguments):
    result = subprocess.run(arguments, capture_output=True, timeout=15, check=True)
    return result.stdout.decode("utf-8").strip()


def revision(value):
    return value if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", value) else None


def image_id(value):
    return value if isinstance(value, str) and re.fullmatch(r"sha256:[a-f0-9]{64}", value) else None


def image_reference(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_./:@-]{1,256}", value) else None


def runner_summary(payload, now=None):
    """No reports, trip IDs, model outputs, messages or logs leave this function."""
    runner = payload.get("runner") if isinstance(payload, dict) else None
    if not isinstance(runner, dict):
        return {"status": "invalid_response", "ready": False}
    stamp = runner.get("updated_at")
    valid_stamp = (isinstance(stamp, (int, float)) and not isinstance(stamp, bool)
                   and math.isfinite(stamp) and stamp >= 0)
    age = (time.time() if now is None else now) - stamp if valid_stamp else None
    fresh = age is not None and 0 <= age < 10
    return {
        "status": "observed",
        "ready": runner.get("ready") is True and fresh,
        "reported_ready": runner.get("ready") is True,
        "heartbeat_fresh": fresh,
        "updated_at": stamp if valid_stamp else None,
        "runtime_image_reference": image_reference(runner.get("image")),
        "runtime_image_id": image_id(runner.get("image_id")),
    }


def runner_from_url(base_url):
    parsed = urllib.parse.urlsplit(base_url)
    if (parsed.scheme not in {"https", "http"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}):
        return {"status": "invalid_base_url", "ready": False}
    try:
        request = urllib.request.Request(base_url.rstrip("/") + "/api/v3/ml/runs",
                                         headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
        if len(data) > MAX_RESPONSE_BYTES:
            return {"status": "response_too_large", "ready": False}
        return runner_summary(json.loads(data))
    except (OSError, ValueError, TimeoutError):
        # Exceptions may include URLs or server bodies; don't print them.
        return {"status": "unavailable", "ready": False}


def checkout_summary(root):
    try:
        sha = command(["git", "-C", str(root), "rev-parse", "HEAD"])
        dirty = bool(command(["git", "-C", str(root), "status", "--porcelain"]))
        return {"revision": revision(sha), "dirty": dirty,
                "meaning": "local_checkout_not_proof_of_deployed_version"}
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return {"revision": None, "dirty": None, "meaning": "checkout_unavailable"}


def container_summary(container):
    if not container:
        return {"status": "not_requested", "image_id": None, "revision": None}
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", container):
        return {"status": "invalid_container", "image_id": None, "revision": None}
    # Docker emits only these three selected values, never Config.Env or logs.
    template = ('{"image_id":{{json .Image}},"image_reference":{{json .Config.Image}},'
                '"revision":{{json (index .Config.Labels "org.opencontainers.image.revision")}}}')
    try:
        value = json.loads(command(["docker", "inspect", "--type=container", "--format", template, container]))
        return {"status": "observed", "image_id": image_id(value.get("image_id")),
                "image_reference": image_reference(value.get("image_reference")),
                "revision": revision(value.get("revision")),
                "revision_source": "optional_image_label_not_inferred_from_tag"}
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return {"status": "unavailable", "image_id": None, "revision": None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--url", default="https://test.autoxak.ru")
    parser.add_argument("--api-container")
    parser.add_argument("--controller-container")
    args = parser.parse_args()
    report = {
        "schema_version": 1,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "checkout": checkout_summary(args.checkout),
        "api": container_summary(args.api_container),
        "controller": container_summary(args.controller_container),
        "runner": runner_from_url(args.url),
        "ordinary_trip_model": "not_inferred_from_experiment_runner",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    # A responding web page alone is not success. This does not test inference.
    return 0 if report["runner"].get("ready") and report["runner"].get("runtime_image_id") else 1


if __name__ == "__main__":
    raise SystemExit(main())
