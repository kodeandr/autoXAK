"""Smoke command contracts only; real container/browser coverage runs on Linux."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import urllib.error


spec = importlib.util.spec_from_file_location("ml_smoke", Path(__file__).resolve().parents[2] / "scripts/ml_smoke.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
IMAGE = "sha256:" + "a" * 64
MODEL = "b" * 32
RUN = "c" * 32


class SmokeTests(unittest.TestCase):
    def listing(self, **overrides):
        return {"runner": {"ready": True, "updated_at": 1000, "image_id": IMAGE, **overrides}, "runs": []}

    def result(self, **overrides):
        return {"status": "succeeded", "model_id": MODEL, "runtime_image_id": IMAGE,
                "result": {"trips": [{"estimated_fuel_l": .05}], "mae_l": .002,
                           "compared_trips": 1, "accuracy_certified": False}, **overrides}

    def test_default_check_never_writes_and_filters_unrelated_data(self):
        listing = self.listing()
        listing["runs"] = [{"status": "failed", "logs": "PRIVATE_GPS_SECRET"}]
        client = Mock()
        client.request.side_effect = [{"status": "ok"}, listing]
        output = io.StringIO()
        with patch.object(module, "Client", return_value=client), patch.object(module.time, "time", return_value=1001), contextlib.redirect_stdout(output):
            self.assertEqual(module.main([]), 0)
        self.assertEqual(client.request.call_count, 2)
        self.assertTrue(all(len(call.args) == 1 for call in client.request.call_args_list))
        self.assertNotIn("PRIVATE_GPS_SECRET", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["status"], "ready")

    def test_false_stale_future_missing_and_nonfinite_heartbeats_fail(self):
        invalid = [{"ready": False}, {"ready": 1}, {"updated_at": 980}, {"updated_at": 1020},
                   {"updated_at": None}, {"updated_at": True}, {"updated_at": float("nan")},
                   {"updated_at": float("inf")}, {"image_id": "mutable-tag"}]
        for overrides in invalid:
            with self.subTest(overrides=overrides), patch.object(module.time, "time", return_value=1001):
                client = Mock()
                client.request.side_effect = [{}, self.listing(**overrides)]
                with self.assertRaises(module.CheckError):
                    module.readiness(client)

    def test_targets_are_restricted_before_network(self):
        with patch.object(module.urllib.request, "build_opener") as opener:
            for url in ("https://autoxak.ru", "https://evil.test", "http://test.autoxak.ru",
                        "https://test.autoxak.ru?key=secret", "http://user:pass@localhost",
                        "file:///tmp/data", "http://localhost/api", "http://localhost:wrong"):
                with self.subTest(url=url), self.assertRaises(module.CheckError):
                    module.Client(url)
            opener.assert_not_called()
        self.assertEqual(module.validate_url("http://127.0.0.1:8000/"), "http://127.0.0.1:8000")

    def test_http_errors_do_not_expose_body(self):
        client = module.Client("http://localhost:8000")
        client.opener = Mock()
        client.opener.open.side_effect = urllib.error.HTTPError("http://localhost:8000/ready", 503, "SECRET", {}, io.BytesIO(b"SECRET"))
        with self.assertRaisesRegex(module.CheckError, "HTTP 503") as error:
            client.request("/ready")
        self.assertNotIn("SECRET", str(error.exception))

    def test_redirects_are_not_followed(self):
        with self.assertRaises(module.CheckError):
            module.NoRedirect().redirect_request(None, None, 302, "", {}, "https://autoxak.ru")

    def test_busy_queue_does_not_upload(self):
        client = Mock()
        with self.assertRaises(module.CheckError):
            module.smoke(client, {}, [{"status": "running"}])
        client.request.assert_not_called()

    def test_synthetic_run_verifies_fuel_interval_metrics_and_identity(self):
        client = Mock()
        client.request.side_effect = [{"id": MODEL}, {"id": RUN, "status": "queued"}, self.result()]
        summary = {"runtime_image_id": IMAGE}
        module.smoke(client, summary, [])
        self.assertEqual(summary["status"], "succeeded")
        self.assertFalse(summary["accuracy_certified"])
        payload = client.request.call_args_list[1].args[1]
        self.assertEqual(payload["data"]["gps"], [])
        self.assertNotIn("vehicle_id", payload["data"])

    def test_failure_and_wrong_image_never_become_success(self):
        for result in ({"status": "failed", "error": "PRIVATE_LOG"},
                       self.result(runtime_image_id="sha256:" + "d" * 64),
                       self.result(result=None), {"status": "unknown"}):
            with self.subTest(result=result):
                client = Mock()
                client.request.side_effect = [{"id": MODEL}, {"id": RUN, "status": "queued"}, result]
                summary = {"runtime_image_id": IMAGE}
                with self.assertRaises(module.CheckError):
                    module.smoke(client, summary, [])
                self.assertEqual(summary["run_id"], RUN)
                self.assertNotEqual(summary.get("status"), "succeeded")
                self.assertNotIn("PRIVATE_LOG", json.dumps(summary))

    def test_timeout_retains_own_ids_without_deleting_or_retrying_submission(self):
        client = Mock()
        client.request.side_effect = [{"id": MODEL}, {"id": RUN, "status": "queued"}]
        summary = {}
        with self.assertRaisesRegex(module.CheckError, "Timed out"):
            module.smoke(client, summary, [], timeout=0)
        self.assertEqual(client.request.call_count, 2)
        self.assertEqual(summary, {"model_id": MODEL, "run_id": RUN})


if __name__ == "__main__":
    unittest.main()
