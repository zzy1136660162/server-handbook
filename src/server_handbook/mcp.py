"""Optional MCP stdio interface over the same core used by the CLI."""

import argparse
from pathlib import Path
import sys
from typing import Annotated, Any, Literal

from . import __version__
from .config import load_config
from .documents import handoff, register_and_render
from .errors import HandbookError
from .operations import (
    apply_operation, get_operation, list_operations, plan_operation,
)
from .registry import get_service, list_services, read_document
from .runtime import doctor, overview


def create_server(config_path=None, allow_write=False) -> Any:
    """Create a read-only server; writes require an explicit startup option."""
    try:
        from mcp.server import MCPServer
        from mcp.server.mcpserver.exceptions import ResourceError, ToolError
        from mcp.types import ToolAnnotations
        from pydantic import Field
    except ImportError as error:
        raise HandbookError(
            "MCP support requires the optional extra and SDK 2.3.0. "
            "Install 'server-handbook[mcp]' in this application's environment."
        ) from error

    config = load_config(config_path)
    server = MCPServer(
        "server-handbook", version=__version__, log_level="WARNING",
        instructions=(
            "Start with handbook://instructions or handbook_handoff, then locate "
            "the service and read its registered README. Declarations describe "
            "intent; live observations and dated operation receipts are separate. "
            "Never execute descriptor commands strings. Plans do not execute. "
            "This server only provides write tools when started with --allow-write. "
            "A failed or interrupted deployment is not success; inspect its receipt "
            "before retrying. Do not replay database initialization or delete data."
        ),
    )
    read_only = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                                idempotent_hint=True, open_world_hint=False)

    def call(function, *args, **kwargs):
        try:
            return function(config, *args, **kwargs)
        except HandbookError as error:
            raise ToolError(str(error)) from error

    @server.tool(annotations=read_only, structured_output=True)
    def handbook_get_overview(include_runtime: bool = False) -> dict[str, Any]:
        """Get configured roots and server identity; optionally query fresh runtime state.

        Runtime probes are fixed read-only commands. Unknown or unavailable probes
        are not evidence that a service is stopped. No secret configuration is read.
        """
        return call(overview, include_runtime=include_runtime)

    @server.tool(annotations=read_only, structured_output=True)
    def handbook_list_services(
        offset: Annotated[int, Field(ge=0, description="Zero-based starting offset")] = 0,
        limit: Annotated[int, Field(ge=1, le=50, description="Maximum entries per page")] = 20,
    ) -> dict[str, Any]:
        """List registered service summaries. Follow next_offset until it is null.

        The deployment state is a declaration, not a current health check.
        Use handbook_get_service for revision, paths and maintenance details.
        """
        return call(list_services, offset=offset, limit=limit)

    @server.tool(annotations=read_only, structured_output=True)
    def handbook_get_service(
        service_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")],
    ) -> dict[str, Any]:
        """Read one registered declaration, revision and latest operation summary.

        Configuration and secret references are paths only. Commands are display
        text, not instructions this tool executes. Read the returned README next.
        """
        return call(get_service, service_id)

    @server.tool(annotations=read_only, structured_output=True)
    def handbook_read_document(
        path: Annotated[str, Field(min_length=1, max_length=4096,
                                   description="A registered README or workspace entry document")],
    ) -> dict[str, Any]:
        """Read an approved Markdown document, limited to 64 KiB.

        Use paths returned by service details or handoff. Arbitrary files,
        configuration, secrets, and paths outside the configured roots are denied.
        """
        return call(read_document, path)

    @server.tool(annotations=read_only, structured_output=True)
    def handbook_doctor(include_runtime: bool = False) -> dict[str, Any]:
        """Check registry consistency, source drift and unfinished operations.

        Reports errors and warnings without correcting them or restarting services.
        Optional runtime probes do not replace project-specific business checks.
        """
        return call(doctor, include_runtime=include_runtime)

    @server.tool(annotations=read_only, structured_output=True)
    def handbook_list_operations(
        service_id: Annotated[str | None, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")] = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
    ) -> dict[str, Any]:
        """Read the most recent operation receipts, optionally for one service.

        Results are bounded by limit, not a complete audit export. Inspect a
        receipt with handbook_get_operation before retrying unfinished work.
        """
        return {"operations": call(list_operations, service_id=service_id, limit=limit)}

    @server.tool(annotations=read_only, structured_output=True)
    def handbook_get_operation(
        operation_id: Annotated[str, Field(min_length=1, max_length=128)],
    ) -> dict[str, Any]:
        """Read an operation's recorded plan, steps, checks and terminal status.

        A running receipt may describe interrupted work. This query never resumes,
        repeats, recovers, or rolls back commands.
        """
        return call(get_operation, operation_id)

    @server.tool(annotations=read_only, structured_output=True)
    def handbook_plan_operation(
        service_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")],
        action: Literal["up", "start", "restart", "stop", "reload"],
    ) -> dict[str, Any]:
        """Preview a supported Compose/systemd operation and verification scope.

        Does not execute the plan, modify records, or acquire deployment authority.
        The service manager determines which actions are valid.
        """
        return call(plan_operation, service_id, action)

    @server.tool(annotations=read_only, structured_output=True)
    def handbook_handoff() -> dict[str, Any]:
        """Get the zero-context entry points and registered service handoff summary.

        Start here in a fresh session; then read only the relevant service docs.
        """
        return call(handoff)

    if allow_write:
        registration = ToolAnnotations(read_only_hint=False, destructive_hint=False,
                                        idempotent_hint=False, open_world_hint=False)
        deployment = ToolAnnotations(read_only_hint=False, destructive_hint=True,
                                      idempotent_hint=False, open_world_hint=False)

        @server.tool(annotations=registration, structured_output=True)
        def handbook_register_service(
            descriptor_path: Annotated[str, Field(min_length=1, max_length=4096)],
            note: Annotated[str, Field(min_length=1, max_length=2048)],
            expect_revision: Annotated[int | None, Field(ge=1)] = None,
        ) -> dict[str, Any]:
            """Register a reviewed descriptor and append its revision history atomically.

            Requires explicit task authorization. First registration omits
            expect_revision; updates must use the currently observed revision.
            Registration records a declaration and does not deploy or verify it.
            """
            return call(register_and_render, descriptor_path, note,
                        expect_revision=expect_revision)

        @server.tool(annotations=deployment, structured_output=True)
        def handbook_apply_operation(
            service_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")],
            action: Literal["up", "start", "restart", "stop", "reload"],
            expect_revision: Annotated[int, Field(ge=1)],
            note: Annotated[str, Field(min_length=1, max_length=2048)],
        ) -> dict[str, Any]:
            """Execute an authorized, reviewed operation and durably record its checks.

            Preview with handbook_plan_operation and confirm task scope first.
            Requires the current revision and unchanged declaration. Uses fixed
            supported operations; no shell strings, data deletion or automatic
            database initialization. Checks state their actual verification scope.
            """
            result = call(apply_operation, service_id, action, expect_revision, note)
            if result.get("status") != "succeeded":
                raise ToolError(
                    f"Operation {result.get('id', 'unknown')} ended with status "
                    f"{result.get('status', 'unknown')}. Read its receipt using "
                    "handbook_get_operation; do not automatically retry commands."
                )
            return result

    @server.resource("handbook://instructions", mime_type="text/markdown")
    def instructions() -> str:
        """The configured workspace's zero-context Agent instructions."""
        try:
            return read_document(config, str(Path(config["workspace"])/"AGENTS.md"))["text"]
        except HandbookError as error:
            raise ResourceError(str(error)) from error

    @server.resource("handbook://overview", mime_type="application/json")
    def static_overview() -> dict[str, Any]:
        """Configured server identity and service summary without runtime probes."""
        try:
            return overview(config, include_runtime=False)
        except HandbookError as error:
            raise ResourceError(str(error)) from error

    return server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Serve Server Handbook MCP over stdio (read-only by default).")
    parser.add_argument("--config", help="Handbook config path; otherwise use environment or workspace discovery")
    parser.add_argument("--allow-write", action="store_true", help="Expose explicit registration and managed deployment tools")
    args = parser.parse_args(argv)
    try:
        create_server(args.config, allow_write=args.allow_write).run(transport="stdio")
    except HandbookError as error:
        print(f"Server Handbook MCP: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
