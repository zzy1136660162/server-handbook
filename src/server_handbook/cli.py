"""One CLI over the shared validation, registration and execution core."""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .errors import HandbookError


def parser():
    root = argparse.ArgumentParser(description="Server Handbook: server context, deployment registry, and tracked operations.")
    root.add_argument("--version", action="version", version=f"Server Handbook {__version__}")
    root.add_argument("--config", help="Workspace config; also SERVER_HANDBOOK_CONFIG or nearest .server-handbook/config.json")
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Preview initialization; --apply creates only tool-owned files and managed blocks")
    init.add_argument("--workspace", required=True)
    init.add_argument("--name", default="Server Handbook")
    init.add_argument("--project-root", action="append")
    init.add_argument("--service-root", action="append")
    init.add_argument("--records-dir")
    init.add_argument("--apply", action="store_true")
    project = commands.add_parser("project", help="Prepare a new project without claiming a deployment")
    new = project.add_subparsers(dest="project_command", required=True).add_parser("new")
    new.add_argument("id")
    new.add_argument("--name", required=True)
    new.add_argument("--kind", choices=("project", "compose", "systemd"), default="project")
    new.add_argument("--home")
    new.add_argument("--profile", choices=("generic", "python", "node", "java"), default="generic")
    new.add_argument("--apply", action="store_true")
    adopt = commands.add_parser("adopt", help="Prepare an existing project; retain its files and manual notes")
    for name in ("home", "id", "name"):
        adopt.add_argument(f"--{name}", required=True)
    adopt.add_argument("--kind", choices=("project", "compose", "systemd", "desktop", "toolchain"), default="project")
    adopt.add_argument("--compose-file", action="append")
    adopt.add_argument("--compose-project")
    adopt.add_argument("--compose-service")
    adopt.add_argument("--unit", action="append")
    adopt.add_argument("--apply", action="store_true")
    register = commands.add_parser("register", help="Register a reviewed descriptor and refresh its managed README block")
    register.add_argument("file")
    register.add_argument("--note", required=True)
    register.add_argument("--expect-revision", type=int)
    listed = commands.add_parser("list", help="List registered metadata, not live health")
    listed.add_argument("--offset", type=int, default=0)
    listed.add_argument("--limit", type=int, default=20)
    for name in ("show", "history"):
        commands.add_parser(name).add_argument("id")
    commands.add_parser("overview").add_argument("--runtime", action="store_true")
    commands.add_parser("discover", help="Find candidates read-only; never auto-register")
    commands.add_parser("doctor", help="Check source drift, registry integrity and unfinished operations").add_argument("--runtime", action="store_true")
    docs = commands.add_parser("docs")
    render = docs.add_subparsers(dest="docs_command", required=True).add_parser("render")
    render.add_argument("id")
    render.add_argument("--apply", action="store_true")
    commands.add_parser("handoff", help="Compact zero-context entry with next steps")
    skill = commands.add_parser("skill", help="Export the bundled portable Agent Skill without changing client settings")
    export = skill.add_subparsers(dest="skill_command", required=True).add_parser("export")
    export.add_argument("destination")
    export.add_argument("--apply", action="store_true")
    for name in ("plan", "apply"):
        operation = commands.add_parser(name, help="Plan a registered operation" if name == "plan" else "Execute and record a registered operation")
        operation.add_argument("id")
        operation.add_argument("--action", required=True, choices=("up", "start", "restart", "stop", "reload"))
        if name == "apply":
            operation.add_argument("--expect-revision", required=True, type=int)
            operation.add_argument("--note", required=True)
    operations = commands.add_parser("operations")
    operations.add_argument("--service")
    operations.add_argument("--limit", type=int, default=20)
    commands.add_parser("operation").add_argument("id")
    recovery = commands.add_parser("recover", help="Mark an abandoned operation interrupted; never replay or roll back commands")
    recovery.add_argument("id")
    recovery.add_argument("--note", required=True)
    commands.add_parser("mcp", help="Serve stdio MCP; readonly unless explicitly enabled").add_argument("--allow-write", action="store_true")
    return root


def dispatch(args):
    from .config import initialize, load_config
    if args.command == "init":
        return initialize(args.workspace, name=args.name, project_roots=args.project_root,
                          service_roots=args.service_root, records_dir=args.records_dir, apply=args.apply)
    if args.command == "skill":
        from .documents import export_skill
        return export_skill(args.destination, apply=args.apply)
    config = load_config(args.config)
    from . import documents, operations, registry, runtime
    if args.command == "project":
        return documents.project_new(config, args.id, args.name, kind=args.kind, home=args.home,
                                     profile=args.profile, apply=args.apply)
    if args.command == "adopt":
        return documents.adopt(config, args.home, args.id, args.name, kind=args.kind,
                               compose_files=args.compose_file, compose_project=args.compose_project,
                               compose_service=args.compose_service, systemd_units=args.unit, apply=args.apply)
    if args.command == "register":
        return documents.register_and_render(config, args.file, args.note, expect_revision=args.expect_revision)
    if args.command == "list":
        return registry.list_services(config, offset=args.offset, limit=args.limit)
    if args.command == "show":
        return registry.get_service(config, args.id)
    if args.command == "history":
        return {"history": registry.service_history(config, args.id)}
    if args.command == "overview":
        return runtime.overview(config, include_runtime=args.runtime)
    if args.command == "discover":
        return runtime.discover(config)
    if args.command == "doctor":
        return runtime.doctor(config, include_runtime=args.runtime)
    if args.command == "docs":
        return documents.render_service_docs(config, args.id, apply=args.apply)
    if args.command == "handoff":
        return documents.handoff(config)
    if args.command == "plan":
        return operations.plan_operation(config, args.id, args.action)
    if args.command == "apply":
        return operations.apply_operation(config, args.id, args.action, args.expect_revision, args.note)
    if args.command == "operations":
        return {"operations": operations.list_operations(config, service_id=args.service, limit=args.limit)}
    if args.command == "operation":
        return operations.get_operation(config, args.id)
    if args.command == "recover":
        return operations.recover_operation(config, args.id, args.note)
    raise HandbookError("Unknown command; use --help.")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        if args.command == "mcp":
            from .mcp import main as mcp_main
            values = (["--config", args.config] if args.config else []) + (["--allow-write"] if args.allow_write else [])
            return mcp_main(values)
        result = dispatch(args)
        if result is not None:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == "apply" and result["status"] != "succeeded":
            return 1
        if args.command == "doctor" and (not result["valid"] or result["unfinished_operations"]):
            return 1
        return 0
    except ImportError as error:
        if args.command == "mcp":
            print("MCP extra is unavailable. Install 'server-handbook[mcp]' in this environment.", file=sys.stderr)
            return 1
        raise error
    except (HandbookError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
