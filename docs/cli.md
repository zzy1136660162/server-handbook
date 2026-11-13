# CLI reference

JSON is the normal stdout format. Actionable errors go to stderr and return exit code 1. `--help` and `--version` do not require a workspace. Registry/app state is local to the configured records directory; no public database is used.

Select config with global `--config PATH`, `SERVER_HANDBOOK_CONFIG`, or by running within a workspace whose nearest ancestor contains `.server-handbook/config.json`. Explicit `--config` comes before the subcommand.

| Command | Behavior |
| --- | --- |
| `init --workspace PATH [--apply]` | Preview or create config, database, entry documents and configured roots |
| `project new ID --name NAME [--kind project\|compose\|systemd] [--profile generic\|python\|node\|java] [--home PATH] [--apply]` | Prepare a new project; does not register or deploy |
| `adopt --home PATH --id ID --name NAME --kind KIND [--apply]` | Prepare an existing directory without moving its data |
| `register FILE --note TEXT [--expect-revision N]` | Validate declaration, commit snapshot/history and update managed README |
| `list [--offset N --limit N]` | Page through registered metadata |
| `show ID` | Complete service registration and latest operation summary |
| `history ID` | Registered revisions and their actual notes |
| `overview [--runtime]` | Configured roots and optionally fresh fixed host probes |
| `discover` | Read-only candidates from configured roots and Docker metadata |
| `doctor [--runtime]` | Registry integrity, missing docs, source drift and incomplete receipts |
| `docs render ID [--apply]` | Preview or refresh a managed README block |
| `handoff` | Compact agent entry with paths and next steps |
| `skill export DESTINATION [--apply]` | Export bundled Agent Skill; no global client settings changed |
| `plan ID --action ACTION` | Validate deployment inputs and show fixed argv; no execution |
| `apply ID --action ACTION --expect-revision N --note TEXT` | Execute tracked action, checks, receipt and Markdown refresh |
| `operations [--service ID --limit N]` | Compact recent operation summaries |
| `operation OPERATION_ID` | Full persisted receipt |
| `recover OPERATION_ID --note TEXT` | Mark abandoned work interrupted after owner checks; never replay |
| `mcp [--allow-write]` | Launch optional MCP over stdio |

Initialization options: repeat `--project-root PATH` or `--service-root PATH`; use `--records-dir PATH` to keep the SQLite metadata elsewhere. Config always lives under the workspace `.server-handbook/`. Existing incompatible config is not overwritten by init.

Compose adoption additionally accepts repeatable `--compose-file PATH`, `--compose-project NAME`, and `--compose-service NAME`. Supply all three kinds together. systemd adoption accepts repeatable `--unit NAME.service`. Existing descriptors must be reviewed and registered, rather than overwritten by adoption.

Supported actions: Compose `up`, `restart`, `stop`; systemd `start`, `restart`, `stop`, `reload`. The descriptor's kind determines valid actions. The tool does not execute arbitrary metadata commands. Compose dependencies must already be running; this release does not manage a whole stack as a transaction.

Only initialization/preparation/document/Skill export commands use the preview/`--apply` convention. `register` changes the registry; `apply` is itself an explicit execution command and requires revision and note. Failed execution/checks return a persisted failed receipt and a nonzero exit. A doctor result with errors or unfinished operations also returns nonzero.

`verification_scope=process_only` means no configured business probe was run. `process_and_configured_http` reports the declared HTTP checks, not an assurance about every feature. Registering `state=deployed` remains a declaration, not a health check.
