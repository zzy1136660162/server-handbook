"""Anonymous end-to-end workspace flows; no probes touch host services."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from server_handbook import cli, config, documents, registry, runtime
from server_handbook.errors import HandbookError


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.workspace = self.base / "workspace"
        self.probe = patch("server_handbook.runtime._probe", return_value={"status": "unknown", "reason": "Test intentionally has no host access."})
        self.probe_mock = self.probe.start()
        self.addCleanup(self.probe.stop)

    def initialize(self):
        result = config.initialize(self.workspace, apply=True)
        return config.load_config(result["config_path"])

    def new_registered(self, cfg, id="example", kind="project", units=None):
        prepared = documents.project_new(cfg, id, "Anonymous example", kind=kind, apply=True)
        descriptor = prepared["descriptor"]
        if units:
            descriptor["systemd_units"] = units
            descriptor["state"] = "deployed"
            Path(prepared["descriptor_path"]).write_text(json.dumps(descriptor), encoding="utf-8")
        record = documents.register_and_render(cfg, prepared["descriptor_path"], "Tested metadata adoption")
        return prepared, record

    def invoke(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(argv)
            except SystemExit as error:
                code = error.code
        return code, out.getvalue(), err.getvalue()

    def test_init_dryrun_is_zero_write(self):
        result = config.initialize(self.workspace)
        self.assertFalse(result["applied"])
        self.assertFalse(self.workspace.exists())
        self.assertTrue(any(item["path"].endswith("config.json") for item in result["changes"]))
        self.probe_mock.assert_not_called()

    def test_init_and_repeat_preserve_crlf_manual_entry_content(self):
        self.workspace.mkdir()
        manual = {"README.md": b"# Owner notes\r\nKeep these exact line endings.\r\n",
                  "AGENTS.md": b"# Existing team rules\r\nUser-specific instructions.\r\n"}
        for name, data in manual.items():
            (self.workspace / name).write_bytes(data)
        first = config.initialize(self.workspace, apply=True)
        for name, data in manual.items():
            self.assertTrue((self.workspace / name).read_bytes().startswith(data), name)
        before = {name: (self.workspace / name).read_bytes() for name in manual}
        second = config.initialize(self.workspace, apply=True)
        self.assertEqual(second["changes"], [])
        self.assertEqual(config.load_config(first["config_path"])["workspace"], str(self.workspace.resolve()))
        self.assertEqual(before, {name: (self.workspace / name).read_bytes() for name in manual})

    def test_init_different_config_and_broken_markers_do_not_overwrite(self):
        self.initialize()
        target = self.workspace / "README.md"
        before = target.read_bytes()
        with self.assertRaisesRegex(HandbookError, "already configured differently"):
            config.initialize(self.workspace, name="A different server", apply=True)
        self.assertEqual(target.read_bytes(), before)
        target.write_bytes(b"Owner text\r\n<!-- server-handbook:begin -->\r\nBroken managed block\r\n")
        damaged = target.read_bytes()
        with self.assertRaisesRegex(HandbookError, "markers"):
            config.initialize(self.workspace, apply=True)
        self.assertEqual(target.read_bytes(), damaged)

    def test_custom_roots_and_external_records_are_explicit(self):
        legacy = self.base / "legacy-projects"
        records = self.base / "records"
        result = config.initialize(self.workspace, project_roots=[legacy], records_dir=records, apply=True)
        cfg = config.load_config(result["config_path"])
        self.assertEqual(cfg["project_roots"], [str(legacy.resolve())])
        self.assertTrue((records / "state.sqlite3").is_file())
        self.assertEqual(config.contained_path(cfg, legacy / "existing"), legacy / "existing")
        with self.assertRaises(HandbookError):
            config.contained_path(cfg, self.base / "unconfigured")

    def test_load_config_uses_nearest_workspace_or_explicit_environment(self):
        cfg = self.initialize()
        child = self.workspace / "projects" / "nested"
        child.mkdir()
        before = Path.cwd()
        try:
            os.chdir(child)
            with patch.dict(os.environ, {"SERVER_HANDBOOK_CONFIG": ""}):
                self.assertEqual(config.load_config()["workspace"], cfg["workspace"])
            with patch.dict(os.environ, {"SERVER_HANDBOOK_CONFIG": cfg["_config_path"]}):
                os.chdir(self.base)
                self.assertEqual(config.load_config()["workspace"], cfg["workspace"])
        finally:
            os.chdir(before)

    def test_adopt_preserves_existing_source_datastore_and_manual_notes(self):
        cfg = self.initialize()
        home = self.workspace / "projects" / "legacy"
        store = home / "data" / "store"
        store.mkdir(parents=True)
        preserved = {home / "server.py": b"print('existing source')\r\n",
                     store / "database.bin": bytes(range(256)),
                     home / "settings.ini": b"ordinary existing configuration\r\n"}
        for path, data in preserved.items():
            path.write_bytes(data)
        manual = b"# Existing project\r\nManual recovery instructions.\r\n"
        (home / "README.md").write_bytes(manual)
        (home / "AGENTS.md").write_bytes(b"# Existing rules\r\nKeep repository style.\r\n")
        prepared = documents.adopt(cfg, home, "legacy", "Legacy", apply=True)
        self.assertFalse(prepared["registered"])
        self.assertEqual(prepared["descriptor"]["state"], "prepared")
        self.assertEqual(registry.list_services(cfg)["total"], 0)
        for path, data in preserved.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertTrue((home / "README.md").read_bytes().startswith(manual))
        self.assertFalse((store / "README.md").exists())
        self.assertEqual(set(p.name for p in store.iterdir()), {"database.bin"})

    def test_adopt_plan_has_no_writes_and_refuses_existing_declaration(self):
        cfg = self.initialize()
        home = self.workspace / "projects" / "legacy"
        home.mkdir()
        (home / "README.md").write_bytes(b"# Original\r\n")
        plan = documents.adopt(cfg, home, "legacy", "Legacy")
        self.assertFalse(plan["applied"])
        self.assertFalse((home / "deploy").exists())
        self.assertEqual((home / "README.md").read_bytes(), b"# Original\r\n")
        documents.adopt(cfg, home, "legacy", "Legacy", apply=True)
        declaration = Path(plan["descriptor_path"])
        original = declaration.read_bytes()
        with self.assertRaisesRegex(HandbookError, "already exists"):
            documents.adopt(cfg, home, "legacy", "Renamed", apply=True)
        self.assertEqual(declaration.read_bytes(), original)

    def test_project_new_profiles_and_directory_readmes(self):
        cfg = self.initialize()
        for profile in documents.PROFILES:
            with self.subTest(profile=profile):
                created = documents.project_new(cfg, profile, f"Example {profile}", profile=profile, apply=True)
                home = Path(created["home"])
                for directory in ("src", "deploy", "config", "data", "logs", "docs"):
                    self.assertTrue((home / directory / "README.md").is_file(), directory)
                self.assertIn(documents.PROFILES[profile], (home / "AGENTS.md").read_text(encoding="utf-8"))
                self.assertFalse((home / "data" / "store").exists())
                self.assertEqual(created["descriptor"]["state"], "prepared")
        self.assertEqual(registry.list_services(cfg)["total"], 0)

    def test_project_preview_refuses_existing_or_outside_home(self):
        cfg = self.initialize()
        preview = documents.project_new(cfg, "pending", "Pending")
        self.assertFalse(Path(preview["home"]).exists())
        with self.assertRaises(HandbookError):
            documents.project_new(cfg, "bad", "Bad", home=self.base / "unconfigured", apply=True)
        Path(preview["home"]).mkdir()
        with self.assertRaisesRegex(HandbookError, "already exists"):
            documents.project_new(cfg, "pending", "Pending", apply=True)

    def test_register_and_render_preserve_manual_sections_and_source_hash(self):
        cfg = self.initialize()
        home = self.workspace / "projects" / "manual"
        home.mkdir()
        (home / "README.md").write_bytes(b"# Existing operations\r\nKeep manual notes.\r\n")
        prepared = documents.adopt(cfg, home, "manual", "Manual", apply=True)
        readme = home / "README.md"
        readme.write_bytes(readme.read_bytes() + b"\r\nManual tail remains.\r\n")
        original = readme.read_bytes()
        prefix = original.split(config.BEGIN.encode(), 1)[0]
        suffix = original.split(config.END.encode(), 1)[1]
        source = Path(prepared["descriptor_path"])
        source_before = source.read_bytes()
        first = documents.register_and_render(cfg, source, "Reviewed existing project")
        self.assertEqual(first["documentation"]["status"], "updated")
        self.assertTrue(readme.read_bytes().startswith(prefix))
        self.assertTrue(readme.read_bytes().endswith(suffix))
        self.assertEqual(source.read_bytes(), source_before)
        self.assertEqual(first["descriptor_sha256"], hashlib.sha256(source_before).hexdigest())
        self.assertEqual(registry.service_history(cfg, "manual")[0]["record"]["revision"], 1)
        self.assertEqual(registry.validate_registry(cfg), [])
        self.assertIn("does not prove a successful deployment", readme.read_text(encoding="utf-8"))

    def test_render_preview_does_not_write_and_registration_render_failure_is_explicit(self):
        cfg = self.initialize()
        prepared, first = self.new_registered(cfg)
        readme = Path(prepared["descriptor"]["readme"])
        readme.write_bytes(b"# Human text\r\nNo managed block yet.\r\n")
        before = readme.read_bytes()
        result = documents.render_service_docs(cfg, "example")
        self.assertTrue(result["changed"])
        self.assertFalse(result["applied"])
        self.assertEqual(readme.read_bytes(), before)
        readme.write_bytes(b"# Human notes\r\n<!-- server-handbook:begin -->\r\nBroken block\r\n")
        damaged = readme.read_bytes()
        second = documents.register_and_render(cfg, prepared["descriptor_path"], "Metadata only; docs need review", expect_revision=1)
        self.assertEqual(second["documentation"]["status"], "failed")
        self.assertEqual(second["revision"], 2)
        self.assertEqual(readme.read_bytes(), damaged)
        self.assertEqual([row["revision"] for row in registry.service_history(cfg, "example")], [1, 2])

    def test_doctor_reports_source_drift_and_unfinished_operation_separately(self):
        cfg = self.initialize()
        prepared, first = self.new_registered(cfg)
        self.assertTrue(runtime.doctor(cfg)["valid"])
        source = Path(prepared["descriptor_path"])
        descriptor = json.loads(source.read_text(encoding="utf-8"))
        descriptor["name"] = "Edited but not registered"
        source.write_text(json.dumps(descriptor), encoding="utf-8")
        with registry.database(cfg, write=True) as connection:
            document = {"id": "op-abandoned", "service_id": "example", "status": "running"}
            connection.execute("INSERT INTO operations VALUES(?,?,?,?)", ("op-abandoned", "example", "running", json.dumps(document)))
        checked = runtime.doctor(cfg)
        self.assertFalse(checked["valid"])
        self.assertTrue(any("SHA-256" in error for error in checked["errors"]))
        self.assertTrue(any("op-abandoned" in warning for warning in checked["warnings"]))
        self.assertEqual(checked["unfinished_operations"][0]["id"], "op-abandoned")
        self.probe_mock.assert_not_called()

    def test_overview_runtime_queries_each_registered_systemd_unit(self):
        cfg = self.initialize()
        self.new_registered(cfg, kind="systemd", units=["example.service"])
        def probe(argv):
            if argv[0] == "docker":
                return {"status": "unknown", "reason": "No Docker access in test"}
            if argv[0] == "ip":
                return {"status": "ok", "output": "[]"}
            self.assertEqual(argv[-1], "example.service")
            return {"status": "ok", "output": "LoadState=loaded\nActiveState=active\nSubState=running\nUnitFileState=enabled"}
        with patch("server_handbook.runtime.platform.system", return_value="Linux"), patch("server_handbook.runtime._probe", side_effect=probe):
            observed = runtime.overview(cfg, include_runtime=True)
        self.assertEqual(observed["runtime"]["systemd"]["example.service"]["ActiveState"], "active")
        self.assertEqual(observed["runtime"]["docker"]["status"], "unknown")

    def test_discover_only_candidates_and_does_not_parse_compose_contents(self):
        cfg = self.initialize()
        home = self.workspace / "projects" / "candidate"
        home.mkdir()
        compose = home / "compose.yaml"
        compose.write_bytes(b"not valid UTF8 or YAML: \xff\xfe")
        before = compose.read_bytes()
        found = runtime.discover(cfg)
        self.assertFalse(found["registered"])
        self.assertEqual(found["candidates"][0]["state"], "unconfirmed")
        self.assertEqual(found["candidates"][0]["compose_files"], [str(compose)])
        self.assertEqual(registry.list_services(cfg)["total"], 0)
        self.assertEqual(compose.read_bytes(), before)

    def test_handoff_exposes_entry_without_claiming_runtime_success(self):
        cfg = self.initialize()
        self.new_registered(cfg)
        entry = documents.handoff(cfg)
        self.assertEqual(entry["agent_entry"], str(self.workspace / "AGENTS.md"))
        self.assertEqual(entry["services"][0]["id"], "example")
        self.assertTrue(any("separate" in item for item in entry["boundaries"]))
        self.probe_mock.assert_not_called()

    def test_cli_help_and_version_do_not_require_valid_configuration(self):
        with patch.dict(os.environ, {"SERVER_HANDBOOK_CONFIG": str(self.base / "missing.json")}):
            for argv in (["--help"], ["project", "new", "--help"], ["--version"]):
                code, out, err = self.invoke(argv)
                self.assertEqual(code, 0)
                self.assertIn("Server Handbook" if argv == ["--version"] else "usage:", out)
                self.assertEqual(err, "")

    def test_cli_json_output_and_errors_use_separate_streams(self):
        cfg = self.initialize()
        self.new_registered(cfg)
        code, out, err = self.invoke(["--config", cfg["_config_path"], "list"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["total"], 1)
        self.assertEqual(err, "")
        code, out, err = self.invoke(["--config", cfg["_config_path"], "show", "missing"])
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "")
        self.assertIn("not registered", err)
        code, out, err = self.invoke(["--config", cfg["_config_path"], "list", "--limit", "0"])
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "")
        self.assertIn("limit", err)

    def test_cli_process_help_and_dryrun_init(self):
        source = Path(__file__).resolve().parents[1] / "src"
        env = {**os.environ, "PYTHONPATH": str(source), "SERVER_HANDBOOK_CONFIG": str(self.base / "missing.json")}
        help_result = subprocess.run([sys.executable, "-X", "utf8", "-m", "server_handbook.cli", "--help"],
                                     capture_output=True, text=True, encoding="utf-8", env=env, timeout=15)
        self.assertEqual(help_result.returncode, 0)
        self.assertEqual(help_result.stderr, "")
        planned = subprocess.run([sys.executable, "-X", "utf8", "-m", "server_handbook.cli", "init", "--workspace", str(self.workspace)],
                                 capture_output=True, text=True, encoding="utf-8", env=env, timeout=15)
        self.assertEqual(planned.returncode, 0)
        self.assertFalse(json.loads(planned.stdout)["applied"])
        self.assertEqual(planned.stderr, "")
        self.assertFalse(self.workspace.exists())


if __name__ == "__main__":
    unittest.main()
