# Using the shared server workflow

Commands below assume the workspace config is discoverable. Otherwise add `--config /absolute/path/to/.server-handbook/config.json` before the subcommand. Use `--help` to confirm installed options.

## Locate and inspect

```bash
server-handbook handoff
server-handbook list
server-handbook show SERVICE_ID
server-handbook doctor
```

Follow returned README paths and the recorded Compose files or systemd units. `overview --runtime` adds current host observations. Missing permissions return unknown, not stopped. Descriptor `commands` values are reference text and are never an execution mechanism.

## Prepare or adopt

`project new` and `adopt` preview by default. `--apply` writes their reviewed preparation files; neither deploys or registers a service. Adoption retains the existing home and manual documentation. Review discovered candidates before deciding they are current services.

Fill `home/deploy/registry/SERVICE_ID.json` with actual metadata. Keep credentials in their existing controlled configuration, and record only paths in `secret_refs`. State `prepared` remains honest until actual deployment is established. Optional profiles guide development without replacing repository-specific conventions.

## Register and deploy

```bash
server-handbook register /absolute/service-home/deploy/registry/SERVICE_ID.json --note 'Actual metadata change and known verification scope'
server-handbook plan SERVICE_ID --action up
server-handbook apply SERVICE_ID --action up --expect-revision REVISION --note 'Purpose of this authorized deployment'
server-handbook operations --service SERVICE_ID
server-handbook operation OPERATION_ID
server-handbook doctor
```

On an existing declaration, registration also requires `--expect-revision REVISION`. Resolve conflicts by rereading and comparing; do not overwrite blindly. Compose `up/restart/stop` applies only to the named service; dependencies must already be running. systemd `start/restart/stop/reload` applies to the registered units.

Read the receipt even if the process started. It distinguishes execution failure, check failure, process-only verification, configured HTTP verification, and documentation refresh failure. For business acceptance beyond those probes, use the project's existing tests under the user's task and record the additional outcome honestly in maintenance notes.

## Incomplete work

An unfinished operation blocks another managed action on the same service. Inspect both the receipt and current state. `recover OPERATION_ID --note ...` refuses a live owner and marks abandoned work interrupted. It never performs rollback, retries commands or resets data. Build a new plan only after understanding remaining effects.
