# Server Handbook

Server context, deployment registration, and tracked operations for humans and AI agents.

[中文说明](https://github.com/zzy1136660162/server-handbook/blob/main/README.zh-CN.md) · [Architecture](https://github.com/zzy1136660162/server-handbook/blob/main/docs/architecture.md) · [CLI reference](https://github.com/zzy1136660162/server-handbook/blob/main/docs/cli.md) · [MCP setup](https://github.com/zzy1136660162/server-handbook/blob/main/docs/mcp.md)

[Source](https://github.com/zzy1136660162/server-handbook) · [Report an issue](https://github.com/zzy1136660162/server-handbook/issues) · [PyPI](https://pypi.org/project/server-handbook/) · [Publishing guide](https://github.com/zzy1136660162/server-handbook/blob/main/docs/pypi-publishing.zh-CN.md)

Server Handbook gives a new agent a predictable place to start, records where services live, and saves actual results from managed Compose/systemd actions. Existing projects can stay in their current directories. Manual README and AGENTS content outside marked blocks is preserved.

**Status:** 0.1.0 alpha, available on [PyPI](https://pypi.org/project/server-handbook/0.1.0/). Core commands use only the Python standard library. MCP is an optional extra. Service execution targets Linux/Compose; clients and registry operations are portable.

## Install

Python 3.11 or newer is required. Use an isolated environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install 'server-handbook[mcp]==0.1.0'
server-handbook --help
```

On Windows activate `.venv\Scripts\Activate.ps1`, or call `.venv\Scripts\python.exe` directly. If pipx is installed, use `pipx install 'server-handbook[mcp]==0.1.0'`. Omit `[mcp]` to install only the standard-library CLI.

To install from source instead, obtain the checkout and run the final command inside an activated isolated environment:

```bash
git clone https://github.com/zzy1136660162/server-handbook.git
cd server-handbook
python -m pip install '.[mcp]'
```

## Start a workspace

```bash
# Default is a preview: no directories or files are created.
server-handbook init --workspace /srv/workspace
server-handbook init --workspace /srv/workspace --apply
cd /srv/workspace
server-handbook handoff
server-handbook project new demo --name 'Demo application' --kind compose
server-handbook project new demo --name 'Demo application' --kind compose --apply
```

The generated declaration is `projects/demo/deploy/registry/demo.json`. It starts as `prepared`. Add the actual Compose project, service, files, ports, data references, and optional HTTP checks. Review the project README and existing rules. Preparation does not deploy an application.

```bash
server-handbook register /srv/workspace/projects/demo/deploy/registry/demo.json \
  --note 'Initial reviewed deployment declaration; service not yet started'
server-handbook plan demo --action up
server-handbook apply demo --action up --expect-revision 1 \
  --note 'Start the reviewed demo service and run its declared checks'
server-handbook operations --service demo
server-handbook doctor
```

Use `show demo` to read the current revision instead of copying the example number for an existing service. `apply` automatically retains real execution/check results and refreshes the managed README block. A process check alone is not business acceptance; configured HTTP probes are recorded separately.

## Adopt an existing project

Configure additional roots during initialization. Existing source, data and runtime directories are not moved:

```bash
server-handbook init --workspace /srv/workspace --project-root /opt/apps --apply
server-handbook --config /srv/workspace/.server-handbook/config.json adopt \
  --home /opt/apps/example --id example --name 'Existing application' --kind compose \
  --compose-file /opt/apps/example/compose.yaml \
  --compose-project example --compose-service web
```

Review the preview, then repeat with `--apply`. Adoption prepares files and instructions; registration is explicit. Existing declarations are never silently replaced. `discover` produces unconfirmed candidates without reading configuration values or auto-registering containers.

## One source for each kind of information

- `<service-home>/deploy/registry/<id>.json`: the editable service declaration.
- `<workspace>/.server-handbook/config.json`: host name, directory roots and records location.
- `<records-dir>/state.sqlite3`: registered snapshots/history and managed operation receipts, committed transactionally.
- `AGENTS.md` and service `README.md`: human/agent guidance; generated content stays between `server-handbook` markers.
- Runtime observations: queried on request, timestamped, and distinct from declarations and old receipts.

SQLite state is local application metadata, not an application business database. It needs no MySQL, cloud account, server daemon, or model API key. The tool never starts an LLM itself.

## MCP

```bash
server-handbook-mcp --config /srv/workspace/.server-handbook/config.json
```

The default interface is read-only stdio. Configure the client to launch this command, locally or through your existing SSH connection. Installing the server alone does not enroll every AI client automatically. `--allow-write` explicitly adds only registration and managed apply tools; it does not expose an arbitrary shell. See [MCP setup](https://github.com/zzy1136660162/server-handbook/blob/main/docs/mcp.md).

## What is recorded

Managed operations record their plan and input hashes before commands execute, then save bounded step outcomes, actual check scope, status, timestamps, and documentation status. A service can have only one active managed operation. Failed preflight/commands/checks remain failed; interrupted work stays visible and is never automatically replayed.

```bash
server-handbook operation OPERATION_ID
server-handbook recover OPERATION_ID --note 'Reviewed abandoned work; inspect live state before a fresh plan'
```

Recovery refuses a live owner and marks abandoned work interrupted; it neither rolls back external effects nor repeats commands. Compose operations manage the named service with dependencies left to the existing deployment process. No `down`, volume deletion, automatic database initialization, generic shell tool, or global privileged daemon is provided.

Direct shell changes outside this tool are not fully attributable. Source drift may be detected later, but the tool cannot reconstruct every change or its purpose. A registered `deployed` state is not proof of current health. Paths in `secret_refs` are references, not a channel for returning credentials.

## Develop and build

```bash
python -m pip install -e '.[mcp,dev]'
python -m pytest
python -m build
```

Most service-runner tests use isolated temporary workspaces and mocked commands; real loopback HTTP and stdio MCP tests are included. The optional Docker integration test is explicitly enabled with `SERVER_HANDBOOK_DOCKER_TESTS=1`; it uses only its own unique project and cleans it up. See [verification](https://github.com/zzy1136660162/server-handbook/blob/main/docs/verification.md) for the checks actually performed and their limits.

Contributions: [CONTRIBUTING.md](https://github.com/zzy1136660162/server-handbook/blob/main/CONTRIBUTING.md). License: [MIT](https://github.com/zzy1136660162/server-handbook/blob/main/LICENSE).
