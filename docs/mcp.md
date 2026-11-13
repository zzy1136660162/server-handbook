# MCP and agent setup

Install the optional extra in the application's isolated environment. SDK 2.3.0 is the tested version for this release. The default server is stdio and does not open an HTTP listener.

```bash
server-handbook-mcp --config /srv/workspace/.server-handbook/config.json
```

Configure a client that supports custom stdio commands with its required format. This is a generic `mcpServers` fragment, not a claim that all clients accept the same config format:

```json
{
  "mcpServers": {
    "server-handbook": {
      "command": "/absolute/path/to/server-handbook-mcp",
      "args": ["--config", "/srv/workspace/.server-handbook/config.json"]
    }
  }
}
```

For a remote host, an existing key-authenticated SSH connection can carry stdio:

```json
{
  "mcpServers": {
    "server-handbook": {
      "command": "ssh",
      "args": ["-T", "server-alias", "/absolute/path/to/server-handbook-mcp", "--config", "/srv/workspace/.server-handbook/config.json"]
    }
  }
}
```

Configure the SSH alias, identity and host-key verification separately. Never echo login hints into the stdout of noninteractive SSH commands: stdout is the MCP wire. Banners/MOTD must use their normal SSH mechanisms. Installing this Python package does not edit sshd, authorized_keys or AI client settings.

## Default tools and resources

Read-only tools: `handbook_get_overview`, `handbook_list_services`, `handbook_get_service`, `handbook_read_document`, `handbook_doctor`, `handbook_list_operations`, `handbook_get_operation`, `handbook_plan_operation`, `handbook_handoff`.

Resources: `handbook://instructions` (workspace AGENTS) and `handbook://overview` (static context). Begin with the instructions or handoff, then read the selected service's README. Lists are paginated and operations are summaries until requested individually.

To expose writes, deliberately add `--allow-write` to the server startup command. This adds `handbook_register_service` and `handbook_apply_operation`; no general shell, initialization, deletion or unrestricted filesystem tool is added. Apply requires the current expected revision and a purpose. Failures return an error containing the saved operation ID, so the agent can inspect its receipt before retrying.

## Portable Skill

Export the bundled workflow to a destination supported by your client:

```bash
server-handbook skill export /absolute/client-skills/server-handbook
server-handbook skill export /absolute/client-skills/server-handbook --apply
```

This command needs no server config. It refuses to overwrite a differing Skill file, and changes no global client setting. Read `SKILL.md` for the procedure. The Skill changes how a supporting agent approaches the task; it does not grant permissions or guarantee that every client will discover it automatically.

## Optional SSH login hint

An administrator can add a short static MOTD script using the actual workspace path. For example, a separately reviewed `/etc/update-motd.d/05-server-handbook` may contain:

```sh
#!/bin/sh
printf '\nServer guide: /srv/workspace/AGENTS.md\n'
printf 'Start here: server-handbook --config /srv/workspace/.server-handbook/config.json handoff\n\n'
```

Use the host distribution's login mechanism; do not append unconditional echo statements to shell startup files. Keep login hints static and cheap. This is an optional administrator action, and has not been performed by package installation.
