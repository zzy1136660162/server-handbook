"""Managed operations are tested with fake runtimes; no host services are changed."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error

from server_handbook.config import initialize, load_config
from server_handbook.errors import HandbookError
from server_handbook import operations
from server_handbook.registry import database, get_service, register_service


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        initialize(self.root, apply=True)
        self.config = load_config(self.root/".server-handbook/config.json")
        self.home = self.root/"services/demo"
        (self.home/"deploy/registry").mkdir(parents=True)
        (self.home/"README.md").write_text("# Demo\n", encoding="utf-8")
        self.compose = self.home/"compose.json"
        self.compose.write_text('{"services":{"web":{"image":"example.invalid/web:1"}}}', encoding="utf-8")
        self.source = self.home/"deploy/registry/demo.json"
        self.descriptor = {"schema_version": 1, "id": "demo", "name": "Demo", "kind": "compose",
                           "state": "installed", "home": str(self.home), "readme": str(self.home/"README.md"),
                           "compose": {"project": "demo-project", "service": "web", "files": [str(self.compose)]}}
        self.register()
        self.addCleanup(patch.stopall)
        patch.object(operations, "VERIFY_ATTEMPTS", 1).start()
        patch.object(operations, "_render_docs", return_value=None).start()

    def register(self, revision=None):
        self.source.write_text(json.dumps(self.descriptor), encoding="utf-8")
        return register_service(self.config, self.source, "Temporary fixture", expect_revision=revision)

    def runtime(self, argv, cwd, timeout=60):
        if "ps" in argv:
            stdout = json.dumps({"Service": "web", "Project": "demo-project", "State": "running", "Health": ""})
        else:
            stdout = ""
        return {"argv": argv, "returncode": 0, "status": "succeeded", "stdout": stdout, "duration_ms": 1}

    def apply(self, action="up", revision=1):
        return operations.apply_operation(self.config, "demo", action, revision, "Update test service")

    def test_plan_is_readonly_and_scopes_single_service(self):
        database_file = Path(self.config["records_dir"])/"state.sqlite3"
        before = hashlib.sha256(database_file.read_bytes()).hexdigest()
        with patch.object(operations, "_run") as run:
            plan = operations.plan_operation(self.config, "demo", "up")
        run.assert_not_called()
        self.assertEqual(before, hashlib.sha256(database_file.read_bytes()).hexdigest())
        self.assertEqual(plan["commands"][0][-4:], ["up", "--detach", "--no-deps", "web"])
        self.assertEqual(plan["preflight"][0][-2:], ["config", "--quiet"])
        self.assertEqual(operations.list_operations(self.config), [])

    def test_real_results_are_durable_and_raw_output_is_not_recorded(self):
        def runtime(argv, cwd, timeout=60):
            result = self.runtime(argv, cwd, timeout)
            if "ps" not in argv:
                result["stdout"] = "PASSWORD=do-not-record-this"
            with database(self.config) as connection:
                self.assertEqual(connection.execute("SELECT status FROM operations").fetchone()["status"], "running")
            return result
        with patch.object(operations, "_run", side_effect=runtime) as run:
            receipt = self.apply()
        self.assertEqual(receipt["status"], "succeeded")
        self.assertEqual(receipt["docs_status"], "updated")
        self.assertTrue(receipt["process_verified"])
        self.assertIsNone(receipt["business_verified"])
        self.assertNotIn("do-not-record-this", json.dumps(receipt))
        self.assertEqual(run.call_count, 3)
        self.assertEqual(operations.get_operation(self.config, receipt["id"]), receipt)
        self.assertNotIn("steps", operations.list_operations(self.config)[0])
        self.assertEqual(get_service(self.config, "demo")["last_operation"]["id"], receipt["id"])

    def test_revision_and_source_drift_refuse_before_claim_or_execution(self):
        with patch.object(operations, "_run") as run:
            for invalid in (None, 0, True, 2):
                with self.subTest(revision=invalid), self.assertRaises(HandbookError):
                    self.apply(revision=invalid)
            self.source.write_text(self.source.read_text()+"\n", encoding="utf-8")
            with self.assertRaises(HandbookError):
                self.apply()
        run.assert_not_called()
        self.assertEqual(operations.list_operations(self.config), [])

    def test_nonzero_preflight_prevents_mutation_and_records_failure(self):
        def fail(argv, cwd, timeout=60):
            return {"argv": argv, "returncode": 7, "status": "failed", "stdout": "secret-value", "error": "Command failed."}
        with patch.object(operations, "_run", side_effect=fail) as run:
            receipt = self.apply()
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(receipt["steps"][0]["returncode"], 7)
        self.assertNotIn("secret-value", json.dumps(receipt))

    def test_fixed_runner_hides_stderr_and_handles_timeout(self):
        with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired(["docker"], 1)):
            result = operations._run(["docker", "compose", "version"], str(self.home), timeout=1)
        self.assertEqual(result["status"], "failed")
        self.assertIn("timed out", result["error"])
        self.assertNotIn("stderr", result)
        with patch.object(subprocess, "run", side_effect=FileNotFoundError):
            result = operations._run(["nonexistent"], str(self.home))
        self.assertEqual(result["status"], "failed")
        self.assertIn("unavailable", result["error"])

    def test_http_failure_cannot_become_success(self):
        self.descriptor["checks"] = [{"type": "http", "url": "http://127.0.0.1:18080/health", "expected_status": 200, "timeout_seconds": 1}]
        self.register(1)
        with patch.object(operations, "_run", side_effect=self.runtime), patch.object(operations, "_http_probe", return_value={"type": "http", "passed": False, "actual_status": 503}):
            receipt = self.apply(revision=2)
        self.assertEqual(receipt["status"], "failed")
        self.assertTrue(receipt["process_verified"])
        self.assertFalse(receipt["business_verified"])
        self.assertEqual(receipt["verification_scope"], "process_and_configured_http")

    def test_business_check_success_is_explicit(self):
        self.descriptor["checks"] = [{"type": "http", "url": "http://127.0.0.1:18080/health", "expected_status": 200, "timeout_seconds": 1}]
        self.register(1)
        with patch.object(operations, "_run", side_effect=self.runtime), patch.object(operations, "_http_probe", return_value={"type": "http", "passed": True, "actual_status": 200}):
            receipt = self.apply(revision=2)
        self.assertEqual(receipt["status"], "succeeded")
        self.assertTrue(receipt["business_verified"])

    def test_overlapping_operations_and_active_recovery_are_refused(self):
        def interrupt(argv, cwd, timeout=60):
            raise KeyboardInterrupt
        with patch.object(operations, "_run", side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            self.apply()
        receipt = operations.get_operation(self.config, operations.list_operations(self.config)[0]["id"])
        self.assertEqual(receipt["status"], "running")
        with patch.object(operations, "_run") as run:
            with self.assertRaises(HandbookError):
                self.apply()
            with self.assertRaises(HandbookError):
                operations.recover_operation(self.config, receipt["id"], "Try recovery")
        run.assert_not_called()
        with patch.object(operations, "_owner_running", return_value=False), patch.object(operations, "_run") as run:
            recovered = operations.recover_operation(self.config, receipt["id"], "Inspected actual service after the interrupted CLI exited")
        run.assert_not_called()
        self.assertEqual(recovered["status"], "interrupted")
        with patch.object(operations, "_run", side_effect=self.runtime):
            self.assertEqual(self.apply()["status"], "succeeded")

    def test_input_change_during_execution_fails_before_next_step(self):
        def runtime(argv, cwd, timeout=60):
            result = self.runtime(argv, cwd, timeout)
            self.compose.write_text(self.compose.read_text()+"\n", encoding="utf-8")
            return result
        with patch.object(operations, "_run", side_effect=runtime) as run:
            receipt = self.apply()
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(run.call_count, 1)

    def test_document_error_keeps_successful_execution_and_receipt(self):
        with patch.object(operations, "_run", side_effect=self.runtime), patch.object(operations, "_render_docs", side_effect=OSError("private exception detail")):
            receipt = self.apply()
        self.assertEqual(receipt["status"], "succeeded")
        self.assertEqual(receipt["docs_status"], "failed")
        self.assertNotIn("private exception detail", json.dumps(receipt))

    def test_stop_verifies_stopped_state_without_http(self):
        self.descriptor["checks"] = [{"type": "http", "url": "http://127.0.0.1:18080/health", "expected_status": 200, "timeout_seconds": 1}]
        self.register(1)
        def stopped(argv, cwd, timeout=60):
            result = self.runtime(argv, cwd, timeout)
            if "ps" in argv:
                result["stdout"] = "[]"
            return result
        with patch.object(operations, "_run", side_effect=stopped), patch.object(operations, "_http_probe") as probe:
            receipt = self.apply("stop", revision=2)
        probe.assert_not_called()
        self.assertEqual(receipt["status"], "succeeded")
        self.assertEqual(receipt["verification_scope"], "process_only")

    def test_unexpected_runtime_rows_do_not_verify_named_service(self):
        descriptor = self.descriptor
        for output in ("not-json", "{}", json.dumps({"Service": "other", "State": "running"}), "[]"):
            self.assertFalse(operations._compose_state(output, descriptor, stopping=False)["passed"])

    def test_systemd_only_uses_registered_units(self):
        self.descriptor.pop("compose")
        self.descriptor.update(kind="systemd", systemd_units=["demo.service", "demo.socket"])
        self.register(1)
        def runtime(argv, cwd, timeout=60):
            result = self.runtime(argv, cwd, timeout)
            if "show" in argv:
                result["stdout"] = "LoadState=loaded\nActiveState=active\nSubState=running\n"
            return result
        with patch.object(operations, "_run", side_effect=runtime) as run:
            receipt = self.apply("restart", revision=2)
        self.assertEqual(receipt["status"], "succeeded")
        self.assertEqual(run.call_args_list[0].args[0], ["systemctl", "--no-ask-password", "restart", "--", "demo.service", "demo.socket"])

    def test_http_probe_retains_status_not_response_body_or_query(self):
        check = {"type": "http", "url": "http://127.0.0.1/health?token=not-in-receipt", "expected_status": 200, "timeout_seconds": 1}
        error = urllib.error.HTTPError(check["url"], 503, "Secret text", {}, io.BytesIO(b"private response"))
        with patch("urllib.request.OpenerDirector.open", side_effect=error):
            result = operations._http_probe(check)
        self.assertEqual(result["actual_status"], 503)
        self.assertNotIn("not-in-receipt", json.dumps(result))
        self.assertNotIn("private", json.dumps(result))

    def test_real_loopback_http_probe_does_not_follow_redirects(self):
        visited = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                visited.append(self.path)
                self.send_response(302 if self.path == "/redirect" else 200)
                if self.path == "/redirect":
                    self.send_header("Location", "/health")
                self.end_headers()
                self.wfile.write(b"body is not part of an operation receipt")

            def log_message(self, format, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            check = {"type": "http", "url": f"http://127.0.0.1:{server.server_port}/redirect",
                     "expected_status": 200, "timeout_seconds": 2}
            result = operations._http_probe(check)
            self.assertFalse(result["passed"])
            self.assertEqual(result["actual_status"], 302)
            self.assertEqual(visited, ["/redirect"])
            check["url"] = f"http://127.0.0.1:{server.server_port}/health"
            self.assertTrue(operations._http_probe(check)["passed"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
