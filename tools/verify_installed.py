"""Exercise a locally installed distribution in a disposable anonymous workspace."""
import argparse
import asyncio
from importlib.resources import files
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

import server_handbook


def command(*arguments, expected=0):
    result = subprocess.run([sys.executable, "-m", "server_handbook", *map(str, arguments)],
                            capture_output=True, encoding="utf-8", timeout=30)
    assert result.returncode == expected, (arguments, result.returncode, result.stderr)
    if expected:
        assert not result.stdout.strip() and "Traceback" not in result.stderr
        return result.stderr
    return json.loads(result.stdout)


async def check_mcp(config, workspace, source, evaluation):
    from mcp import Client, StdioServerParameters
    executable = Path(sys.executable).with_name("server-handbook-mcp.exe" if sys.platform == "win32" else "server-handbook-mcp")
    assert executable.is_file(), "MCP console script missing from installed wheel"
    parameters = StdioServerParameters(command=str(executable), args=["--config", str(config)])
    async with Client(parameters) as client:
        tools = (await client.list_tools()).tools
        assert len(tools) == 9
        assert all(t.annotations.read_only_hint and t.output_schema for t in tools)
        resources = (await client.list_resources()).resources
        assert len(resources) == 2
        assert "Server Handbook" in (await client.read_resource("handbook://instructions")).contents[0].text
        async def call(name, **arguments):
            response = await client.call_tool(name, arguments)
            assert not response.is_error, response
            return response.structured_content
        handoff = await call("handbook_handoff")
        listed = await call("handbook_list_services", limit=1)
        assert listed["total"] == 1 and listed["next_offset"] is None
        service = await call("handbook_get_service", service_id=listed["services"][0]["id"])
        document = await call("handbook_read_document", path=service["readme"])
        assert "Handwritten note" in document["text"]
        plan = await call("handbook_plan_operation", service_id="demo", action="up")
        assert (await call("handbook_doctor"))["valid"]
        operations = await call("handbook_list_operations", service_id="demo")
        assert operations["operations"] == []
        for name, arguments in [
            ("handbook_read_document", {"path": str(workspace/"projects/demo/config/private.md")}),
            ("handbook_read_document", {"path": str(workspace.parent/"outside.md")}),
            ("handbook_list_services", {"limit": 51}),
            ("handbook_register_service", {"descriptor_path": str(source), "note": "not exposed"}),
        ]:
            assert (await client.call_tool(name, arguments)).is_error
        actual = [service["id"], service["state"], service["compose"]["project"], service["compose"]["service"],
                  f"{service['local_ports'][0]['bind']}:{service['local_ports'][0]['port']}",
                  next(a for a in plan["commands"][0] if a == "--no-deps"),
                  next(a for a in plan["preflight"][0] if a == "--quiet"),
                  Path(service["descriptor_path"]).name, Path(handoff["agent_entry"]).name,
                  str(bool(service["last_operation"]))]
        pairs = ET.parse(evaluation).getroot().findall("qa_pair")
        assert len(pairs) == len(actual) == 10
        assert [pair.findtext("answer") for pair in pairs] == actual
    writer = StdioServerParameters(command=sys.executable,
        args=["-m", "server_handbook", "--config", str(config), "mcp", "--allow-write"])
    data = json.loads(source.read_text(encoding="utf-8"))
    data["notes"].append("Anonymous metadata update through installed stdio MCP")
    source.write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    async with Client(writer) as client:
        assert len((await client.list_tools()).tools) == 11
        response = await client.call_tool("handbook_register_service", {
            "descriptor_path": str(source), "note": "Anonymous installed-wheel metadata test", "expect_revision": 1})
        assert not response.is_error, response
        assert response.structured_content["revision"] == 2
        assert response.structured_content["documentation"]["status"] == "updated"
    return {"stdio_readonly_tools": 9, "stdio_resources": 2, "negative_checks": 4,
            "write_mode_tools": 11, "write_registration_revision": 2,
            "tool_answerability_checks": 10, "llm_accuracy_evaluation": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-only", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    module_path = Path(server_handbook.__file__).resolve()
    assert "site-packages" in module_path.parts, "Use a clean wheel installation, not an editable source checkout"
    assets = files("server_handbook").joinpath("assets")
    assert json.loads(assets.joinpath("schemas/service.schema.json").read_text(encoding="utf-8"))["$schema"]
    evidence = {"status": "passed", "version": server_handbook.__version__, "module_path": str(module_path),
                "python": sys.version, "base_only": args.base_only, "schema_asset": True}
    with tempfile.TemporaryDirectory(prefix="handbook-wheel-") as temporary:
        workspace = Path(temporary)/"workspace-交接"
        preview = command("init", "--workspace", workspace)
        assert not preview["applied"] and not workspace.exists()
        workspace.mkdir()
        manual = b"# Owner notes\r\nHandwritten note must survive.\r\n"
        (workspace/"README.md").write_bytes(manual)
        initial = command("init", "--workspace", workspace, "--name", "Anonymous server", "--apply")
        config = Path(initial["config_path"])
        assert (workspace/"README.md").read_bytes().startswith(manual)
        args_for = ["--config", config]
        prepared = command(*args_for, "project", "new", "demo", "--name", "Demo web", "--kind", "compose", "--apply")
        home = Path(prepared["home"])
        source = Path(prepared["descriptor_path"])
        with (home/"README.md").open("ab") as stream:
            stream.write(b"\nHandwritten note for the service.\n")
        (home/"config/private.md").write_text("Not an approved public document", encoding="utf-8")
        compose = home/"compose.json"
        compose.write_text(json.dumps({"services": {"web": {"image": "nginx:alpine"}}}), encoding="utf-8")
        descriptor = prepared["descriptor"]
        descriptor.update(compose={"project": "handbook-demo", "service": "web", "files": [str(compose)]},
            local_ports=[{"bind": "127.0.0.1", "port": 18080, "transport": "tcp", "purpose": "Anonymous example HTTP"}],
            checks=[{"type": "http", "url": "http://127.0.0.1:18080/", "expected_status": 200, "timeout_seconds": 5}])
        source.write_text(json.dumps(descriptor, indent=2)+"\n", encoding="utf-8")
        registered = command(*args_for, "register", source, "--note", "Anonymous reviewed metadata; no deployment executed")
        assert registered["revision"] == 1 and registered["documentation"]["status"] == "updated"
        assert command(*args_for, "doctor")["valid"]
        command(*args_for, "register", source, "--note", "Refuse missing expected revision", expected=1)
        destination = Path(temporary)/"client-skill"
        assert command("skill", "export", destination, "--apply")["applied"]
        assert (destination/"SKILL.md").read_bytes() == assets.joinpath("skills/server-handbook/SKILL.md").read_bytes()
        evidence.update(cli_roundtrip=True, unicode_workspace=True, handwritten_crlf_preserved=True,
                        stale_revision_refused=True, skill_export=True)
        if args.base_only:
            assert importlib.util.find_spec("mcp") is None
            error = command(*args_for, "mcp", expected=1)
            assert "optional extra" in error.lower() or "mcp" in error.lower()
            evidence["no_mcp_dependency_needed_for_core"] = True
        else:
            evaluation = Path(__file__).resolve().parents[1]/"docs/mcp-evaluation.xml"
            evidence.update(asyncio.run(check_mcp(config, workspace, source, evaluation)))
            assert command(*args_for, "show", "demo")["revision"] == 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in evidence.items() if k not in ("module_path", "python")}, ensure_ascii=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
