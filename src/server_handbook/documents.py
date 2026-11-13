"""Project preparation and managed Markdown, preserving human-authored content."""
from __future__ import annotations

import hashlib
from importlib.resources import files
import json
from pathlib import Path
import shlex

from .config import atomic_write, contained_path, managed_text
from .errors import HandbookError
from .registry import descriptor_template, get_service, list_services, validate_descriptor

PROFILES = {
    "generic": "Follow existing repository conventions. State the user task and acceptance criteria before changes. Run the project's real checks; do not invent results.",
    "python": "Reuse the project's Python environment, formatter and test runner. Keep dependencies declared in its existing packaging file. Use isolated environments, not global pip installs.",
    "node": "Use the repository's existing package manager and lockfile. Keep secrets and runtime data outside source output. Run the declared build, lint and task-relevant tests.",
    "java": "Follow the project's existing controller/service/data-access boundaries, transaction and migration conventions. Business authorization belongs in the service layer. Reuse existing MyBatis/XML and response conventions; do not introduce a second migration executor.",
}
DIRECTORY_GUIDES = {
    "src": "Application source or an existing repository. Keep generated output and persistent data outside source control.",
    "deploy": "Compose files, systemd definitions and deployment sources. The registry declaration is in registry/<service-id>.json. Never rerun first-time data initialization for routine maintenance.",
    "config": "Host-specific application configuration. The registry records paths, not credential values. Do not publish production secrets with the open-source tool.",
    "data": "Persistent application data. Give programs their own store/ subdirectories. Do not place README files inside a database engine's actual store directory.",
    "logs": "Application logs when file logging is needed. Record rotation and retention in the service README; do not copy entire logs into handoff context.",
    "docs": "Deployment notes, verification scope and recovery instructions. Automatic execution receipts are stored by Server Handbook in its configured records directory.",
}


def _cli(config):
    path = config.get("_config_path") or str(Path(config["workspace"])/".server-handbook/config.json")
    return "server-handbook --config " + shlex.quote(path)


def _markdown_snapshot(path: Path) -> bytes | None:
    try:
        if path.is_symlink():
            raise HandbookError(f"Review the existing Markdown symlink manually: {path}")
        data = path.read_bytes()
        if len(data) > 65536:
            raise HandbookError(f"Managed Markdown exceeds 64 KiB; split the document first: {path}")
        data.decode("utf-8")
        return data
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as error:
        raise HandbookError(f"Cannot safely read Markdown at {path}; no overwrite was made.") from error


def _descriptor_json(descriptor) -> bytes:
    return (json.dumps(descriptor, ensure_ascii=False, indent=2)+"\n").encode("utf-8")


def _prepare(config, home, service_id, name, kind, profile, *, create, apply,
             compose_files=None, compose_project=None, compose_service=None, systemd_units=None):
    if profile not in PROFILES:
        raise HandbookError("profile must be generic, python, node, or java.")
    home = contained_path(config, home, field="home")
    if create and home.exists():
        raise HandbookError("Project directory already exists; use adopt to preserve an existing project.")
    if not create and not home.is_dir():
        raise HandbookError("adopt requires an existing project directory; use project new for a new one.")
    descriptor = descriptor_template(config, service_id, str(home), name, kind)
    if any((compose_files, compose_project, compose_service)):
        if not all((compose_files, compose_project, compose_service)) or kind != "compose":
            raise HandbookError("Compose adoption needs kind=compose, files, project, and service together.")
        descriptor["compose"] = {"project": compose_project, "service": compose_service,
                                 "files": [str(contained_path(config, path, field="compose file")) for path in compose_files]}
    if systemd_units:
        if kind != "systemd":
            raise HandbookError("Use kind=systemd when providing unit names.")
        descriptor["systemd_units"] = list(systemd_units)
    descriptor = validate_descriptor(config, descriptor)
    source = home/"deploy/registry"/f"{service_id}.json"
    resolved_source = contained_path(config, source, field="descriptor destination")
    if not resolved_source.is_relative_to(home):
        raise HandbookError("Descriptor destination must remain inside the service home after resolving symlinks.")
    if source.exists() or source.is_symlink():
        raise HandbookError(f"Deployment declaration already exists; inspect and register it instead: {source}")
    readme = home/"README.md"
    readme_before = _markdown_snapshot(readme)
    content = readme_before.decode("utf-8") if readme_before is not None else ""
    body = (f"## Server Handbook deployment\n\nService ID: `{service_id}`. Name: {name}.\n"
            f"Home: `{home}`. Declaration: `deploy/registry/{service_id}.json`.\n"
            "Preparation status: **prepared**, not a runtime or business verification.\n\n"
            "Before registering, document the purpose, version, access endpoints, dependencies, startup/log commands, "
            "configuration and data locations, backup/recovery procedure, and actual verification limits.\n"
            "Fill the declaration's real metadata; command strings are reference text, never automatically executed.\n\n"
            f"Register: `{_cli(config)} register {shlex.quote(str(source))} --note '<actual change and verification>'`.\n"
            f"Then run `{_cli(config)} show {service_id}` and `{_cli(config)} doctor`.\n")
    files = {source: _descriptor_json(descriptor), readme: managed_text(content, body).encode("utf-8")}
    originals = {source: None, readme: readme_before}
    agents = home/"AGENTS.md"
    agent_body = ("## Server Handbook workflow\n\n"
                  f"Registered service ID: `{service_id}` (registration may still be pending).\n"
                  f"Workspace entry: `{_cli(config)} handoff`. Use this explicit config even when the project lives outside the workspace.\n"
                  "Read this project's README and the workspace handoff before changes. Existing project rules and user instructions remain authoritative.\n"
                  f"Profile: **{profile}**. {PROFILES[profile]}\n"
                  "Keep code, deployment files, configuration, data, and operational docs in their assigned locations.\n"
                  "Use a plan and expected registry revision for managed deployments. Inspect real check results.\n"
                  "For metadata changes update deploy/registry/<id>.json, register the current revision, then render docs.\n"
                  "Never replay interrupted work or initialization scripts without inspecting live state.\n"
                  "Report verification scope and remaining gaps; shell edits outside this tool are not fully tracked.")
    agents_before = _markdown_snapshot(agents)
    files[agents] = managed_text(agents_before.decode("utf-8") if agents_before is not None else "", agent_body).encode("utf-8")
    originals[agents] = agents_before
    directories = [home/"deploy/registry"]
    if create:
        for directory, guide in DIRECTORY_GUIDES.items():
            directories.append(home/directory)
            files[home/directory/"README.md"] = (f"# {directory}\n\n{guide}\n").encode("utf-8")
            originals[home/directory/"README.md"] = None
    changes = [{"path": str(path), "action": "update_managed_block" if path.exists() else "create"}
               for path in files]
    if apply:
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
        for path, data in files.items():
            before = hashlib.sha256(originals[path]).hexdigest() if originals[path] is not None else None
            atomic_write(path, data, expected_sha256=before, create_only=before is None)
    return {"applied": apply, "home": str(home), "descriptor_path": str(source), "descriptor": descriptor,
            "changes": changes, "registered": False, "next": "Review the descriptor, then register it explicitly."}


def project_new(config, id, name, *, kind="project", home=None, profile="generic", apply=False) -> dict:
    target = home or str(Path(config["project_roots"][0])/id)
    return _prepare(config, target, id, name, kind, profile, create=True, apply=apply)


def adopt(config, home, id, name, *, kind="project", compose_files=None, compose_project=None,
          compose_service=None, systemd_units=None, apply=False) -> dict:
    return _prepare(config, home, id, name, kind, "generic", create=False, apply=apply,
                    compose_files=compose_files, compose_project=compose_project,
                    compose_service=compose_service, systemd_units=systemd_units)


def render_service_docs(config, id, apply=False) -> dict:
    service = get_service(config, id)
    readme = contained_path(config, service["readme"], field="readme")
    source = contained_path(config, service["descriptor_path"], field="descriptor")
    last = service.get("last_operation")
    lines = ["## Server Handbook deployment", "", f"Service: **{service['name']}** (`{service['id']}`).",
             f"Declared state: `{service['state']}`; registry revision: **{service['revision']}**.",
             f"Home: `{service['home']}`.", f"Declaration: `{source}`.",
             f"Registered at: `{service['recorded_at']}`. Declaration state is not live health.", ""]
    for field, label in (("config_paths", "Configuration locations"), ("data_paths", "Persistent data"),
                         ("secret_refs", "Credential references (paths only)"), ("depends_on", "Dependencies")):
        if service.get(field):
            lines += [f"{label}: " + ", ".join(f"`{item}`" for item in service[field])+".", ""]
    if service.get("local_ports"):
        lines += ["| Bind | Port | Transport | Purpose |", "| --- | --- | --- | --- |"]
        for port in service["local_ports"]:
            purpose = port["purpose"].replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {port['bind']} | {port['port']} | {port['transport']} | {purpose} |")
        lines.append("")
    if last:
        lines += [f"Latest managed operation: `{last['id']}`; status: `{last['status']}`.",
                  "Read its complete receipt with `server-handbook operation <id>`; checks describe their actual scope.", ""]
    else:
        lines += ["No managed execution receipt yet; registration alone does not prove a successful deployment.", ""]
    lines += [f"Inspect: `{_cli(config)} show {id}`. Query fresh state: `{_cli(config)} overview --runtime`.",
              "After editing the declaration, register with the current expected revision and run `server-handbook doctor`.",
              "Manual notes outside this block are retained. Changes made outside this tool may only be detected as drift."]
    original_bytes = _markdown_snapshot(readme)
    original = original_bytes.decode("utf-8") if original_bytes is not None else ""
    result = managed_text(original, "\n".join(lines)).encode("utf-8")
    before = hashlib.sha256(original_bytes).hexdigest() if original_bytes is not None else None
    changed = original_bytes != result
    if apply and changed:
        atomic_write(readme, result, expected_sha256=before, create_only=before is None)
    return {"path": str(readme), "changed": changed, "applied": apply, "service_id": id,
            "registry_revision": service["revision"]}


def handoff(config) -> dict:
    listed = list_services(config, offset=0, limit=50)
    return {"name": config["name"], "workspace": config["workspace"], "config_path": config.get("_config_path"),
            "agent_entry": str(Path(config["workspace"])/"AGENTS.md"), "project_roots": config["project_roots"],
            "service_roots": config["service_roots"], "records_dir": config["records_dir"],
            "services": listed["services"], "total_services": listed["total"], "next_offset": listed["next_offset"],
            "workflow": ["Read AGENTS and the target README", "Inspect registry, drift and live state", "Review the managed operation plan",
                         "Apply with expected revision and actual purpose", "Inspect checks, operation receipt and updated docs"],
            "boundaries": ["Registered metadata, current observations and dated receipts are separate.",
                           "Direct shell edits are not automatically attributable; diagnose drift before retrying.",
                           "Only configured Markdown and registered READMEs may be read through MCP."]}


def register_and_render(config, file, note, expect_revision=None) -> dict:
    from .registry import register_service
    record = register_service(config, file, note, expect_revision=expect_revision)
    try:
        documentation = render_service_docs(config, record["id"], apply=True)
        documentation["status"] = "updated"
    except (HandbookError, OSError):
        documentation = {"status": "failed", "note": "Registration committed; review the README and run docs render. No deployment was executed."}
    return {**record, "documentation": documentation}


def export_skill(destination, apply=False) -> dict:
    from .config import absolute_path
    destination = absolute_path(destination, "skill destination")
    resource = files("server_handbook").joinpath("assets/skills/server-handbook")
    copied = []
    for relative in ("SKILL.md", "references/workflow.md"):
        data = resource.joinpath(relative).read_bytes()
        target = destination/relative
        if not target.resolve().is_relative_to(destination) or target.is_symlink():
            raise HandbookError("Skill destination contains a symlink outside the selected directory.")
        if target.exists() and target.read_bytes() != data:
            raise HandbookError(f"Skill file differs; review it before replacing: {target}")
        copied.append((target, data))
    if apply:
        for target, data in copied:
            if not target.exists():
                atomic_write(target, data, create_only=True)
    return {"applied": apply, "destination": str(destination), "files": [str(p) for p, _ in copied],
            "note": "Configure this location in the target client's supported skills directory; no global client settings were changed."}
