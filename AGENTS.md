# Server Handbook development

Users are self-hosted Linux server owners and their authorized coding agents. The core tasks are zero-context handoff, adopting existing directories, and recording managed deployments. Success means real checks and durable records agree, existing data/manual documentation remain intact, and unfinished work is visible.

This is an independent local open-source project. Do not connect to or modify the existing family server, copy private files, or import its real inventory. Use anonymous temporary fixtures. No package/repository publication without explicit instructions.

Use Python standard library for the CLI and local SQLite state; MCP is an optional extra. All entry points share validation and persistence. Declarations, live observations, and operation receipts are distinct. Never execute descriptor `commands` strings. Failures and interruptions must not become success. Preserve user-authored README/AGENTS content outside clearly managed blocks. No shell=True, automatic data deletion, generic shell MCP tools, or global root daemon.

Run meaningful tests, build wheel/sdist, inspect package contents, install a wheel into an isolated environment, and test real stdio MCP. Report simulated service tests separately from real Linux/Docker checks.
