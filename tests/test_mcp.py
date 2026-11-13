"""Protocol tests with mocked host actions; no Docker or systemd is executed."""

from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

try:
    from mcp import Client
    from mcp.server import MCPServer
except ImportError:
    Client = None

from server_handbook.errors import HandbookError


READ_TOOLS = {
    "handbook_get_overview", "handbook_list_services", "handbook_get_service",
    "handbook_read_document", "handbook_doctor", "handbook_list_operations",
    "handbook_get_operation", "handbook_plan_operation", "handbook_handoff",
}
WRITE_TOOLS = {"handbook_register_service", "handbook_apply_operation"}


@unittest.skipIf(Client is None, "Install the mcp extra (SDK 2.3.0) to run protocol tests")
class MCPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from server_handbook import mcp as handbook_mcp
        self.module = handbook_mcp
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.config = {"schema_version": 1, "workspace": str(self.workspace)}
        self.configuration = patch.object(handbook_mcp, "load_config", return_value=self.config)
        self.config_loader = self.configuration.start()
        self.addCleanup(self.configuration.stop)

    async def test_default_surface_is_readonly_and_structured(self):
        async with Client(self.module.create_server("fixture.json")) as client:
            tools = (await client.list_tools()).tools
            self.assertEqual({tool.name for tool in tools}, READ_TOOLS)
            for tool in tools:
                self.assertTrue(tool.annotations.read_only_hint)
                self.assertEqual(tool.output_schema["type"], "object")
            resources = (await client.list_resources()).resources
            self.assertEqual({str(resource.uri) for resource in resources},
                             {"handbook://instructions", "handbook://overview"})

    async def test_service_pagination_forwards_to_shared_core(self):
        expected = {"services": [{"id": "api"}], "total": 4, "next_offset": 3}
        with patch.object(self.module, "list_services", return_value=expected) as core:
            async with Client(self.module.create_server()) as client:
                result = await client.call_tool("handbook_list_services", {"offset": 2, "limit": 1})
                self.assertFalse(result.is_error)
                self.assertEqual(result.structured_content, expected)
                core.assert_called_once_with(self.config, offset=2, limit=1)

    async def test_invalid_arguments_never_reach_core(self):
        with patch.object(self.module, "list_services") as listing, patch.object(self.module, "get_service") as service:
            async with Client(self.module.create_server()) as client:
                for arguments in ({"limit": 51}, {"limit": 0}, {"offset": -1}):
                    result = await client.call_tool("handbook_list_services", arguments)
                    self.assertTrue(result.is_error)
                result = await client.call_tool("handbook_get_service", {"service_id": "--bad-option"})
                self.assertTrue(result.is_error)
            listing.assert_not_called()
            service.assert_not_called()

    async def test_expected_core_errors_are_actionable_tool_errors(self):
        with patch.object(self.module, "read_document", side_effect=HandbookError("Read a registered README; config files are denied.")):
            async with Client(self.module.create_server()) as client:
                result = await client.call_tool("handbook_read_document", {"path": str(self.workspace/"config/private.md")})
                self.assertTrue(result.is_error)
                self.assertIn("registered README", result.content[0].text)
                self.assertIsNone(result.structured_content)

    async def test_shared_queries_and_plan_do_not_apply_operations(self):
        with patch.object(self.module, "get_service", return_value={"id": "api", "revision": 3}), \
             patch.object(self.module, "overview", return_value={"runtime": {"status": "unknown"}}) as overview, \
             patch.object(self.module, "doctor", return_value={"valid": False, "errors": ["Unfinished operation"]}), \
             patch.object(self.module, "plan_operation", return_value={"service_id": "api", "action": "restart"}) as plan, \
             patch.object(self.module, "apply_operation") as apply:
            async with Client(self.module.create_server()) as client:
                self.assertEqual((await client.call_tool("handbook_get_service", {"service_id": "api"})).structured_content["revision"], 3)
                self.assertEqual((await client.call_tool("handbook_get_overview", {})).structured_content["runtime"]["status"], "unknown")
                report = await client.call_tool("handbook_doctor", {})
                self.assertFalse(report.is_error)
                self.assertFalse(report.structured_content["valid"])
                self.assertEqual((await client.call_tool("handbook_plan_operation", {"service_id": "api", "action": "restart"})).structured_content["action"], "restart")
            overview.assert_called_once_with(self.config, include_runtime=False)
            plan.assert_called_once_with(self.config, "api", "restart")
            apply.assert_not_called()

    async def test_recent_operations_and_handoff_use_shared_core(self):
        with patch.object(self.module, "list_operations", return_value=[{"id": "operation-1", "status": "failed"}]) as listing, \
             patch.object(self.module, "get_operation", return_value={"id": "operation-1", "status": "failed"}) as receipt, \
             patch.object(self.module, "handoff", return_value={"entry": "AGENTS.md"}) as handoff:
            async with Client(self.module.create_server()) as client:
                operations = await client.call_tool("handbook_list_operations", {"service_id": "api", "limit": 5})
                self.assertEqual(operations.structured_content["operations"][0]["status"], "failed")
                self.assertEqual((await client.call_tool("handbook_get_operation", {"operation_id": "operation-1"})).structured_content["status"], "failed")
                self.assertEqual((await client.call_tool("handbook_handoff", {})).structured_content["entry"], "AGENTS.md")
            listing.assert_called_once_with(self.config, service_id="api", limit=5)
            receipt.assert_called_once_with(self.config, "operation-1")
            handoff.assert_called_once_with(self.config)

    async def test_resources_read_entry_and_static_overview(self):
        with patch.object(self.module, "read_document", return_value={"text": "# Fixture instructions"}) as read, \
             patch.object(self.module, "overview", return_value={"name": "Fixture"}) as overview:
            async with Client(self.module.create_server()) as client:
                result = await client.read_resource("handbook://instructions")
                self.assertEqual(result.contents[0].text, "# Fixture instructions")
                result = await client.read_resource("handbook://overview")
                self.assertIn("Fixture", result.contents[0].text)
            read.assert_called_once_with(self.config, str(self.workspace/"AGENTS.md"))
            overview.assert_called_once_with(self.config, include_runtime=False)

    async def test_writes_require_optin_and_explicit_fields(self):
        with patch.object(self.module, "apply_operation", return_value={"id": "operation-1", "status": "succeeded"}) as apply:
            async with Client(self.module.create_server(allow_write=True)) as client:
                tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                self.assertEqual(set(tools), READ_TOOLS | WRITE_TOOLS)
                self.assertFalse(tools["handbook_apply_operation"].annotations.read_only_hint)
                self.assertTrue(tools["handbook_apply_operation"].annotations.destructive_hint)
                self.assertFalse(tools["handbook_register_service"].annotations.destructive_hint)
                for arguments in ({"service_id": "api", "action": "restart", "note": "reason"},
                                  {"service_id": "api", "action": "restart", "expect_revision": 1}):
                    self.assertTrue((await client.call_tool("handbook_apply_operation", arguments)).is_error)
                apply.assert_not_called()
                result = await client.call_tool("handbook_apply_operation", {"service_id": "api", "action": "restart", "expect_revision": 3, "note": "Authorized update"})
                self.assertFalse(result.is_error)
                self.assertEqual(result.structured_content["status"], "succeeded")
            apply.assert_called_once_with(self.config, "api", "restart", 3, "Authorized update")

    async def test_failed_operation_is_tool_error_with_receipt_id(self):
        with patch.object(self.module, "apply_operation", return_value={"id": "operation-failed", "status": "failed"}):
            async with Client(self.module.create_server(allow_write=True)) as client:
                result = await client.call_tool("handbook_apply_operation", {"service_id": "api", "action": "up", "expect_revision": 2, "note": "Authorized deployment"})
                self.assertTrue(result.is_error)
                self.assertIn("operation-failed", result.content[0].text)
                self.assertIn("handbook_get_operation", result.content[0].text)

    async def test_registration_uses_expected_revision(self):
        with patch.object(self.module, "register_and_render", return_value={"id": "api", "revision": 2}) as register:
            async with Client(self.module.create_server(allow_write=True)) as client:
                result = await client.call_tool("handbook_register_service", {"descriptor_path": str(self.workspace/"api.json"), "expect_revision": 1, "note": "Reviewed declaration"})
                self.assertFalse(result.is_error)
                self.assertEqual(result.structured_content["revision"], 2)
            register.assert_called_once_with(self.config, str(self.workspace/"api.json"), "Reviewed declaration", expect_revision=1)

    async def test_real_registry_and_document_boundary_in_temporary_workspace(self):
        from server_handbook.config import initialize, load_config
        from server_handbook.documents import project_new
        from server_handbook.registry import service_history

        initialized = initialize(self.workspace/"fixture", apply=True)
        config = load_config(initialized["config_path"])
        self.config_loader.return_value = config
        prepared = project_new(config, "api", "Anonymous API fixture", apply=True)
        home = Path(prepared["home"])
        private = home/"config/credentials.md"
        private.write_text("fixture-secret-value", encoding="utf-8")
        descriptor_path = Path(prepared["descriptor_path"])
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        descriptor["secret_refs"] = [str(private)]
        descriptor_path.write_text(json.dumps(descriptor)+"\n", encoding="utf-8")
        readme = home/"README.md"
        readme.write_text("Human deployment experience must remain.\n\n"+readme.read_text(encoding="utf-8"), encoding="utf-8")

        async with Client(self.module.create_server(allow_write=True)) as client:
            registered = await client.call_tool("handbook_register_service", {
                "descriptor_path": str(descriptor_path), "note": "Register anonymous fixture only",
            })
            self.assertFalse(registered.is_error)
            self.assertEqual(registered.structured_content["revision"], 1)

        async with Client(self.module.create_server()) as client:
            page = await client.call_tool("handbook_list_services", {"limit": 1})
            self.assertEqual(page.structured_content["total"], 1)
            self.assertIsNone(page.structured_content["next_offset"])
            record = await client.call_tool("handbook_get_service", {"service_id": "api"})
            self.assertEqual(record.structured_content["state"], "prepared")
            document = await client.call_tool("handbook_read_document", {"path": record.structured_content["readme"]})
            self.assertIn("Human deployment experience must remain.", document.structured_content["text"])
            for path in (str(private), str(self.workspace/"outside.md"), str(home/".."/".."/"config/private.md")):
                result = await client.call_tool("handbook_read_document", {"path": path})
                self.assertTrue(result.is_error)
                self.assertNotIn("fixture-secret-value", "".join(item.text for item in result.content))
            instructions = await client.read_resource("handbook://instructions")
            self.assertIn("Server Handbook", instructions.contents[0].text)
        self.assertEqual(len(service_history(config, "api")), 1)
        self.assertEqual(private.read_text(encoding="utf-8"), "fixture-secret-value")

    def test_main_uses_stdio_and_reports_startup_error(self):
        with patch.object(self.module, "create_server") as create:
            self.assertEqual(self.module.main(["--config", "fixture.json", "--allow-write"]), 0)
            create.assert_called_once_with("fixture.json", allow_write=True)
            create.return_value.run.assert_called_once_with(transport="stdio")
        with patch.object(self.module, "create_server", side_effect=HandbookError("Run init first.")), \
             patch("sys.stderr") as stderr:
            self.assertEqual(self.module.main([]), 1)
            self.assertIn("Run init first", "".join(call.args[0] for call in stderr.write.call_args_list))


if __name__ == "__main__":
    unittest.main()
