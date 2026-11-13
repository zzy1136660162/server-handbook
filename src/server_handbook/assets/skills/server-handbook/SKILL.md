---
name: server-handbook
description: Adopt, inspect, deploy, or hand off projects on a server using Server Handbook's registry and managed operation receipts. Use for service maintenance and server context recovery, not unrelated coding tasks.
---

# Server Handbook

Use the installed CLI or `handbook_*` MCP tools to find the actual service and its maintenance instructions. The user may have chosen custom paths; do not assume `/srv/workspace` or move existing projects.

Start with `handoff` / `handbook_handoff`, then inspect the target service and read its registered README. Read existing workspace and project AGENTS instructions. Preserve explicit user choices and authorization; this skill supplies a workflow, not additional permission.

Treat these as different evidence:

- Declaration: desired metadata, paths and current registry revision.
- Observation: a fresh query, possibly `unknown` if permissions or tools are unavailable.
- Receipt: what a specific managed operation actually executed and verified at a recorded time.

For deployment, inspect the plan and source drift first. Apply only the requested registered service action, with its current expected revision and a meaningful purpose. Inspect the resulting receipt. Process readiness alone does not establish business correctness; report the configured check scope and any missing checks.

For metadata changes, edit the service's declared source file, then register it with the current expected revision. Registration and managed execution update their own README block automatically; retain manual notes. Run doctor and inspect reported drift or unfinished operations before reporting completion.

If work was interrupted, inspect its receipt and live state. Recovery only marks abandoned work interrupted; it does not replay commands or roll back external effects. Do not rerun first-time database initialization as routine maintenance.

Read [the workflow reference](references/workflow.md) when preparing a new project, adopting an old one, or handling an incomplete operation. Direct shell actions outside this tool are not fully recorded; explain such changes rather than claiming a complete audit trail.
