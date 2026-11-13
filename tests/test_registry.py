"""Registry tests use only anonymous temporary workspaces and real local SQLite."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from server_handbook.config import initialize, load_config
from server_handbook.errors import HandbookError
from server_handbook import registry


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "workspace"
        initialized = initialize(self.root, apply=True)
        self.config = load_config(initialized["config_path"])
        self.home = self.root / "services" / "demo"
        self.home.mkdir(parents=True)
        self.readme = self.home / "README.md"
        self.readme.write_text("# Demo\nManual notes.\n", encoding="utf-8")
        self.file = self.home / "deploy" / "registry" / "demo.json"
        self.file.parent.mkdir(parents=True)
        self.descriptor = {"schema_version": 1, "id": "demo", "name": "Demo",
                           "kind": "project", "state": "prepared", "home": str(self.home),
                           "readme": str(self.readme)}
        self.write()

    def write(self, descriptor=None):
        self.file.write_text(json.dumps(descriptor or self.descriptor, ensure_ascii=False), encoding="utf-8")

    def register(self, **kwargs):
        return registry.register_service(self.config, self.file, "Actual checked change", **kwargs)

    def database_hash(self):
        return hashlib.sha256((Path(self.config["records_dir"]) / "state.sqlite3").read_bytes()).hexdigest()

    def test_registration_snapshot_history_and_latest_operation(self):
        first = self.register()
        self.assertEqual(first["revision"], 1)
        self.assertNotIn("last_operation", first)
        self.assertEqual(registry.get_service(self.config, "demo")["last_operation"], None)
        receipt = {"id": "op-1", "service_id": "demo", "status": "succeeded", "action": "up",
                   "registered_revision": 1, "steps": [{"output": "Large internal output"}]}
        with registry.database(self.config, write=True) as connection:
            connection.execute("INSERT INTO operations VALUES(?,?,?,?)", ("op-1", "demo", "succeeded", json.dumps(receipt)))
        shown = registry.get_service(self.config, "demo")
        self.assertEqual(shown["last_operation"]["id"], "op-1")
        self.assertNotIn("steps", shown["last_operation"])
        self.descriptor["state"] = "retired"
        self.write()
        second = self.register(expect_revision=1)
        self.assertEqual(second["revision"], 2)
        self.assertEqual(second["state"], "retired")
        history = registry.service_history(self.config, "demo")
        self.assertEqual([item["revision"] for item in history], [1, 2])
        self.assertEqual(history[0]["record"], first)
        self.assertEqual(history[1]["record"], second)
        self.assertEqual(registry.validate_registry(self.config), [])

    def test_conflicts_and_invalid_input_leave_state_unchanged(self):
        self.register()
        before = self.database_hash()
        for expected in (None, 9, 0, True):
            with self.subTest(expected=expected):
                with self.assertRaises(HandbookError):
                    self.register(expect_revision=expected)
                self.assertEqual(self.database_hash(), before)
        self.descriptor["local_ports"] = [{"bind": "127.0.0.1", "port": 70000, "transport": "tcp", "purpose": "Demo"}]
        self.write()
        with self.assertRaises(HandbookError):
            self.register(expect_revision=1)
        self.assertEqual(self.database_hash(), before)

    def test_history_insert_failure_rolls_back_snapshot_update(self):
        self.register()
        with registry.database(self.config, write=True) as connection:
            connection.execute("CREATE TRIGGER reject_history BEFORE INSERT ON history BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        self.descriptor["name"] = "Changed"
        self.write()
        with self.assertRaises(HandbookError):
            self.register(expect_revision=1)
        self.assertEqual(registry.get_service(self.config, "demo")["name"], "Demo")
        self.assertEqual(len(registry.service_history(self.config, "demo")), 1)

    def test_concurrent_stale_revision_only_one_writer_wins(self):
        self.register()
        def attempt(_):
            try:
                return self.register(expect_revision=1)["revision"]
            except HandbookError as error:
                return str(error)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(results.count(2), 1)
        self.assertEqual(sum(isinstance(x, str) and "Revision conflict" in x for x in results), 1)
        self.assertEqual(len(registry.service_history(self.config, "demo")), 2)

    def test_running_operation_blocks_registration(self):
        self.register()
        with registry.database(self.config, write=True) as connection:
            connection.execute("INSERT INTO operations VALUES(?,?,?,?)", ("unfinished", "demo", "running", "{}"))
        before = self.database_hash()
        with self.assertRaisesRegex(HandbookError, "unfinished operation"):
            self.register(expect_revision=1)
        self.assertEqual(self.database_hash(), before)

    def test_readonly_database_cannot_write_or_create_missing_state(self):
        before = self.database_hash()
        with self.assertRaises(HandbookError):
            with registry.database(self.config) as connection:
                connection.execute("INSERT INTO services VALUES('bad',1,'{}')")
        self.assertEqual(self.database_hash(), before)
        other = {**self.config, "records_dir": str(self.root / "not-created")}
        with self.assertRaisesRegex(HandbookError, "missing"):
            registry.list_services(other)
        self.assertFalse(Path(other["records_dir"]).exists())

    def test_unknown_database_schema_is_not_reinitialized(self):
        database = Path(self.config["records_dir"]) / "state.sqlite3"
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("PRAGMA user_version=99")
        before = self.database_hash()
        with self.assertRaisesRegex(HandbookError, "Unsupported state database"):
            registry.initialize_database(self.config)
        self.assertEqual(self.database_hash(), before)

    def test_existing_unrelated_database_is_not_adopted(self):
        other = {**self.config, "records_dir": str(self.root / "other-db")}
        Path(other["records_dir"]).mkdir()
        database = Path(other["records_dir"]) / "state.sqlite3"
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("CREATE TABLE precious_data (value TEXT)")
            connection.execute("INSERT INTO precious_data VALUES('keep')")
            connection.commit()
        before = database.read_bytes()
        with self.assertRaises(HandbookError):
            registry.initialize_database(other)
        self.assertEqual(database.read_bytes(), before)

    def test_broken_history_prevents_new_registration(self):
        self.register()
        with registry.database(self.config, write=True) as connection:
            connection.execute("DELETE FROM history")
        before = self.database_hash()
        self.assertTrue(any("historical" in error for error in registry.validate_registry(self.config)))
        with self.assertRaisesRegex(HandbookError, "historical"):
            self.register(expect_revision=1)
        self.assertEqual(self.database_hash(), before)

    def test_read_document_whitelist_size_and_secret_references(self):
        self.register()
        self.assertEqual(registry.read_document(self.config, self.readme)["text"], self.readme.read_bytes().decode("utf-8"))
        self.assertIn("Server Handbook", registry.read_document(self.config, self.root / "AGENTS.md")["text"])
        unregistered = self.home / "notes.md"
        unregistered.write_text("Not whitelisted", encoding="utf-8")
        with self.assertRaisesRegex(HandbookError, "Only workspace"):
            registry.read_document(self.config, unregistered)
        with self.assertRaises(HandbookError):
            registry.read_document(self.config, self.root.parent / "outside.md")
        private = self.home / "config"
        private.mkdir()
        secret = private / "password.md"
        secret.write_text("synthetic-secret", encoding="utf-8")
        with self.assertRaises(HandbookError):
            registry.read_document(self.config, secret)
        self.descriptor["secret_refs"] = [str(self.readme)]
        self.write()
        self.register(expect_revision=1)
        with self.assertRaises(HandbookError):
            registry.read_document(self.config, self.readme)
        self.descriptor["secret_refs"] = []
        self.write()
        self.register(expect_revision=2)
        self.readme.write_text("x" * (registry.MAX_DOCUMENT_BYTES + 1), encoding="utf-8")
        with self.assertRaisesRegex(HandbookError, "size limit"):
            registry.read_document(self.config, self.readme)

    def test_symlinks_cannot_escape_configured_roots(self):
        self.register()
        outside = self.root.parent / "outside.md"
        outside.write_text("Outside", encoding="utf-8")
        self.readme.unlink()
        try:
            self.readme.symlink_to(outside)
        except OSError:
            self.skipTest("Creating symlinks is not permitted on this host")
        with self.assertRaises(HandbookError):
            registry.read_document(self.config, self.readme)

    def test_declaration_drift_dependencies_and_command_strings(self):
        self.descriptor["depends_on"] = ["not-yet-adopted"]
        self.descriptor["commands"] = {"start": "DO-NOT-EXECUTE"}
        self.write()
        with patch("subprocess.run", side_effect=AssertionError("No execution during registry access")):
            self.register()
            self.assertEqual(registry.get_service(self.config, "demo")["commands"]["start"], "DO-NOT-EXECUTE")
            errors = registry.validate_registry(self.config)
        self.assertTrue(any("not-yet-adopted" in error for error in errors))
        self.descriptor["name"] = "Unregistered edit"
        self.write()
        self.assertTrue(any("SHA-256" in error for error in registry.validate_registry(self.config)))

    def test_compose_names_files_and_prepared_template(self):
        prepared = registry.descriptor_template(self.config, "demo", self.home, "Demo", "compose")
        self.assertEqual(prepared["state"], "prepared")
        deployed = {**prepared, "state": "deployed"}
        with self.assertRaisesRegex(HandbookError, "requires its compose"):
            registry.validate_descriptor(self.config, deployed)
        file = self.home / "compose.yaml"
        file.write_text("services: {}\n", encoding="utf-8")
        deployed["compose"] = {"project": "demo", "service": "web", "files": [str(file)]}
        self.assertEqual(registry.validate_descriptor(self.config, deployed, check_files=True)["compose"]["service"], "web")
        for field in ("project", "service"):
            bad = copy.deepcopy(deployed)
            bad["compose"][field] = "--help"
            with self.assertRaises(HandbookError):
                registry.validate_descriptor(self.config, bad)
        deployed["compose"]["files"] = [str(self.root / "another-service.yaml")]
        with self.assertRaisesRegex(HandbookError, "inside the service home"):
            registry.validate_descriptor(self.config, deployed)

    def test_checks_reject_invalid_urls_types_and_credentials(self):
        check = {"type": "http", "url": "http://127.0.0.1:8080/health", "expected_status": 200, "timeout_seconds": 5}
        valid = {**self.descriptor, "checks": [check]}
        self.assertEqual(registry.validate_descriptor(self.config, valid)["checks"], [check])
        for field, value in [("url", "file:///etc/passwd"), ("url", "http://user:pass@localhost/health"),
                             ("url", "http://localhost:99999/"), ("url", "http://localhost/\nhealth"),
                             ("timeout_seconds", 0), ("expected_status", True), ("type", "shell")]:
            with self.subTest(field=field, value=value):
                bad = copy.deepcopy(valid)
                bad["checks"][0][field] = value
                with self.assertRaises(HandbookError):
                    registry.validate_descriptor(self.config, bad)

    def test_source_location_utf8_duplicate_keys_and_unknown_fields(self):
        wrong = self.home / "demo.json"
        wrong.write_text(json.dumps(self.descriptor), encoding="utf-8")
        with self.assertRaisesRegex(HandbookError, "Save the declaration"):
            registry.register_service(self.config, wrong, "No write")
        self.file.write_bytes(json.dumps(self.descriptor).encode("utf-16"))
        with self.assertRaisesRegex(HandbookError, "UTF-8"):
            self.register()
        self.file.write_text('{"id":"first","id":"second"}', encoding="utf-8")
        with self.assertRaisesRegex(HandbookError, "duplicate JSON key"):
            self.register()
        bad = {**self.descriptor, "password": "not-accepted"}
        with self.assertRaisesRegex(HandbookError, "Unknown declaration"):
            registry.validate_descriptor(self.config, bad)
        self.assertEqual(registry.list_services(self.config)["total"], 0)

    def test_pagination_and_unknown_service(self):
        self.register()
        other = self.root / "services" / "another"
        other.mkdir()
        (other / "README.md").write_text("# Another", encoding="utf-8")
        file = other / "deploy" / "registry" / "another.json"
        file.parent.mkdir(parents=True)
        file.write_text(json.dumps(registry.descriptor_template(self.config, "another", other, "Another", "project")), encoding="utf-8")
        registry.register_service(self.config, file, "Adopted anonymous example")
        first = registry.list_services(self.config, limit=1)
        self.assertEqual(first["services"][0]["id"], "another")
        self.assertEqual(first["next_offset"], 1)
        self.assertEqual(registry.list_services(self.config, offset=1, limit=1)["services"][0]["id"], "demo")
        for offset, limit in [(-1, 1), (0, 101), (True, 1)]:
            with self.assertRaises(HandbookError):
                registry.list_services(self.config, offset, limit)
        with self.assertRaisesRegex(HandbookError, "not registered"):
            registry.get_service(self.config, "missing")


if __name__ == "__main__":
    unittest.main()
