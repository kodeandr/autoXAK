"""Identity, freshness and secret filtering; no DB, Docker or network required."""

import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("ml_environment", Path(__file__).resolve().parents[2] / "scripts/inspect_ml_environment.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class VersionReportTests(unittest.TestCase):
    def runner(self, **changes):
        return {"runner": {"ready": True, "updated_at": 1000, "image": "autoxak-ml-runtime:test",
                           "image_id": "sha256:" + "a" * 64, **changes},
                "runs": [{"trip_id": "PRIVATE_GPS", "logs": "SECRET_KEY"}]}

    def test_only_allowlisted_fields_are_returned(self):
        result = module.runner_summary(self.runner(message="PASSWORD", secret="TOKEN"), now=1001)
        self.assertTrue(result["ready"])
        self.assertEqual(result["runtime_image_id"], "sha256:" + "a" * 64)
        for private in ("PRIVATE_GPS", "SECRET_KEY", "PASSWORD", "TOKEN", "runs"):
            self.assertNotIn(private, json.dumps(result))

    def test_stale_future_and_nonfinite_heartbeats_are_not_ready(self):
        for stamp in (989, 1002, None, True, float("nan"), float("inf")):
            with self.subTest(stamp=stamp):
                result = module.runner_summary(self.runner(updated_at=stamp), now=1001)
                self.assertFalse(result["ready"])
                json.dumps(result, allow_nan=False)

    def test_malformed_identity_is_not_reported_as_a_version(self):
        result = module.runner_summary(self.runner(image_id="some-tag", image="secret\nvalue"), now=1001)
        self.assertIsNone(result["runtime_image_id"])
        self.assertIsNone(result["runtime_image_reference"])
        self.assertIsNone(module.revision("short-tag"))
        self.assertEqual(module.revision("f" * 40), "f" * 40)

    def test_missing_runner_is_explicit(self):
        for payload in (None, [], {}, {"runner": []}):
            self.assertEqual(module.runner_summary(payload)["status"], "invalid_response")

    def test_credentials_or_query_in_url_are_rejected_before_network(self):
        with patch.object(module.urllib.request, "urlopen") as fetch:
            for url in ("https://user:secret@example.com", "https://example.com?key=secret", "file:///tmp/data"):
                self.assertEqual(module.runner_from_url(url)["status"], "invalid_base_url")
            fetch.assert_not_called()

    def test_container_identity_does_not_infer_commit_from_tag(self):
        output = json.dumps({"image_id": "sha256:" + "b" * 64,
                             "image_reference": "autoxak-test-api:ml-4ec7b62143e7", "revision": None})
        with patch.object(module, "command", return_value=output) as command:
            result = module.container_summary("test-api-1")
        self.assertIsNone(result["revision"])
        self.assertEqual(result["status"], "observed")
        self.assertIn("--format", command.call_args.args[0])
        self.assertNotIn(".Config.Env", " ".join(command.call_args.args[0]))

    def test_docker_error_does_not_leak_stderr(self):
        with patch.object(module, "command", side_effect=module.subprocess.CalledProcessError(1, "docker", stderr="SECRET")):
            result = module.container_summary("test-api-1")
        self.assertEqual(result["status"], "unavailable")
        self.assertNotIn("SECRET", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
