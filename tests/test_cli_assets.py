import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from server_handbook import cli
from server_handbook.documents import export_skill
from server_handbook.errors import HandbookError


class CliAssetTests(unittest.TestCase):
    def test_skill_export_is_previewed_idempotent_and_preserves_existing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/"client-skills/server-handbook"
            self.assertFalse(export_skill(target)["applied"])
            self.assertFalse(target.exists())
            result = export_skill(target, apply=True)
            self.assertTrue(result["applied"])
            entry = target/"SKILL.md"
            self.assertTrue(entry.read_text(encoding="utf-8").startswith("---\nname: server-handbook\n"))
            self.assertTrue((target/"references/workflow.md").is_file())
            original = entry.read_bytes()
            export_skill(target, apply=True)
            self.assertEqual(entry.read_bytes(), original)
            entry.write_text("My custom skill\n", encoding="utf-8")
            with self.assertRaises(HandbookError):
                export_skill(target, apply=True)
            self.assertEqual(entry.read_text(encoding="utf-8"), "My custom skill\n")

    def test_skill_export_cli_needs_no_workspace_and_outputs_json(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(["skill", "export", str(Path(directory)/"skill")]), 0)
            import json
            self.assertFalse(json.loads(output.getvalue())["applied"])

    def test_cli_propagates_mcp_failure_exit_code(self):
        with patch("server_handbook.mcp.main", return_value=1) as run:
            self.assertEqual(cli.main(["--config", "missing.json", "mcp", "--allow-write"]), 1)
            run.assert_called_once_with(["--config", "missing.json", "--allow-write"])


if __name__ == "__main__":
    unittest.main()
