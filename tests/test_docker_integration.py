"""Opt-in real Compose lifecycle; only a unique disposable test project is touched.

Pre-pull nginx:alpine, then set SERVER_HANDBOOK_DOCKER_TESTS=1 to enable.
The fixture binds one random loopback port and uses no volumes or privileged mode.
"""
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.request
import uuid

from server_handbook.config import BEGIN, initialize, load_config
from server_handbook import operations
from server_handbook.registry import get_service, register_service, validate_registry


@unittest.skipUnless(os.environ.get("SERVER_HANDBOOK_DOCKER_TESTS") == "1",
                     "Set SERVER_HANDBOOK_DOCKER_TESTS=1 to run the isolated real Docker fixture")
class RealDockerIntegrationTests(unittest.TestCase):
    def test_managed_compose_lifecycle_and_failed_probe(self):
        self.assertIsNotNone(shutil.which("docker"), "Explicitly enabled Docker integration requires docker on PATH")
        project = "shbtest_" + uuid.uuid4().hex
        self.assertRegex(project, r"^shbtest_[0-9a-f]{32}$")
        results = []
        with tempfile.TemporaryDirectory(prefix="server-handbook-docker-") as directory:
            workspace = Path(directory).resolve()
            initialize(workspace, apply=True)
            config = load_config(workspace/".server-handbook/config.json")
            home = workspace/"services/test-web"
            (home/"deploy/registry").mkdir(parents=True)
            readme = home/"README.md"
            manual_notes = "# Disposable integration fixture\n\nHuman-authored notes must survive rendering.\n"
            readme.write_text(manual_notes, encoding="utf-8")
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            url = f"http://127.0.0.1:{port}/"
            compose_file = home/"compose.json"
            compose_file.write_text(json.dumps({"services": {"web": {
                "image": "nginx:alpine", "pull_policy": "never",
                "ports": [{"target": 80, "published": str(port), "host_ip": "127.0.0.1", "protocol": "tcp"}],
            }}}), encoding="utf-8")
            source = home/"deploy/registry/test-web.json"
            descriptor = {"schema_version": 1, "id": "test-web", "name": "Disposable test web",
                          "kind": "compose", "state": "prepared", "home": str(home), "readme": str(readme),
                          "compose": {"project": project, "service": "web", "files": [str(compose_file)]},
                          "local_ports": [{"bind": "127.0.0.1", "port": port, "transport": "tcp", "purpose": "Isolated test HTTP"}],
                          "checks": [{"type": "http", "url": url, "expected_status": 200, "timeout_seconds": 2}]}

            def register(expected_revision=None):
                source.write_text(json.dumps(descriptor, indent=2)+"\n", encoding="utf-8")
                return register_service(config, source, "Disposable real Docker integration fixture",
                                        expect_revision=expected_revision)

            def apply(action, revision):
                receipt = operations.apply_operation(config, "test-web", action, revision,
                                                     "Exercise only the isolated Docker test project")
                results.append({"action": action, "revision": revision, "status": receipt["status"],
                                "process_verified": receipt["process_verified"],
                                "business_verified": receipt["business_verified"], "docs_status": receipt["docs_status"]})
                self.assertEqual(operations.get_operation(config, receipt["id"]), receipt)
                self.assertEqual(get_service(config, "test-web")["last_operation"]["id"], receipt["id"])
                self.assertEqual(receipt["docs_status"], "updated", json.dumps(receipt, indent=2))
                text = readme.read_text(encoding="utf-8")
                self.assertTrue(text.startswith(manual_notes))
                self.assertEqual(text.count(BEGIN), 1)
                self.assertIn(receipt["id"], text)
                return receipt

            cleanup = ["docker", "compose", "--project-name", project, "-f", str(compose_file),
                       "down", "--timeout", "5"]
            try:
                self.assertEqual(register()["revision"], 1)
                plan = operations.plan_operation(config, "test-web", "up")
                for command in [*plan["preflight"], *plan["commands"], *plan["probes"]]:
                    self.assertEqual(command[command.index("--project-name")+1], project)
                    self.assertIn(str(compose_file), command)
                self.assertEqual(plan["commands"][0][-4:], ["up", "--detach", "--no-deps", "web"])
                first = apply("up", 1)
                self.assertEqual(first["status"], "succeeded", json.dumps(first, indent=2))
                self.assertTrue(first["process_verified"])
                self.assertTrue(first["business_verified"])
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(url, timeout=3) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(b"Welcome to nginx", response.read(65536))

                descriptor["checks"][0]["expected_status"] = 201
                self.assertEqual(register(1)["revision"], 2)
                # The deliberately wrong status is permanent, so avoid the normal readiness wait.
                with patch.object(operations, "VERIFY_ATTEMPTS", 1):
                    failed = apply("up", 2)
                self.assertEqual(failed["status"], "failed", json.dumps(failed, indent=2))
                self.assertTrue(failed["process_verified"])
                self.assertFalse(failed["business_verified"])
                self.assertTrue(any(check.get("actual_status") == 200 and not check["passed"]
                                    for check in failed["checks"] if check["type"] == "http"))

                descriptor["checks"][0]["expected_status"] = 200
                self.assertEqual(register(2)["revision"], 3)
                repaired = apply("up", 3)
                self.assertEqual(repaired["status"], "succeeded", json.dumps(repaired, indent=2))
                self.assertTrue(repaired["business_verified"])
                stopped = apply("stop", 3)
                self.assertEqual(stopped["status"], "succeeded", json.dumps(stopped, indent=2))
                self.assertTrue(stopped["process_verified"])
                self.assertIsNone(stopped["business_verified"])
                self.assertEqual(stopped["verification_scope"], "process_only")
                self.assertFalse(any(check["type"] == "http" for check in stopped["checks"]))
                self.assertEqual(len(operations.list_operations(config, service_id="test-web")), 4)
                self.assertEqual(validate_registry(config, check_files=True), [])
            finally:
                # Cleanup is scoped to the same UUID project and never removes volumes or images.
                self.assertTrue(re.fullmatch(r"shbtest_[0-9a-f]{32}", project))
                self.assertEqual(cleanup[cleanup.index("--project-name")+1], project)
                self.assertEqual(cleanup[cleanup.index("-f")+1], str(compose_file))
                self.assertTrue(compose_file.resolve().is_relative_to(workspace))
                self.assertNotIn("-v", cleanup)
                self.assertNotIn("--volumes", cleanup)
                finished = subprocess.run(cleanup, cwd=home, stdin=subprocess.DEVNULL, capture_output=True,
                                          text=True, encoding="utf-8", errors="replace", timeout=30, check=False)
                self.assertEqual(finished.returncode, 0, "Isolated test cleanup failed: "+finished.stderr[-2000:])
        print(json.dumps({"fixture": "real_docker_compose", "project": project,
                          "loopback_only": True, "volumes": False, "operations": results,
                          "cleanup": "same_uuid_project_only"}, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
