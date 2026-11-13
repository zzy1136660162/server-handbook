# Anonymous Compose example

This example is a source template, not a running deployment. Copy it into an allowed service home, update paths, inspect the files, and register the declaration.

The sample uses service ID `demo`, Compose project `handbook-demo`, service `web`, loopback HTTP `127.0.0.1:18080`, and initial declaration state `prepared`. The image is `nginx:alpine`; pin an image digest according to your own deployment process before relying on a repeatable release. Nothing in package installation pulls or starts it.

```bash
server-handbook project new demo --name 'Demo web' --kind compose --apply
# Copy compose.yaml into the new project. Adjust the declaration's absolute paths.
# Use service.json as a schema example; do not register unedited template paths.
server-handbook register /srv/workspace/projects/demo/deploy/registry/demo.json --note 'Reviewed anonymous demo metadata'
server-handbook plan demo --action up
server-handbook apply demo --action up --expect-revision 1 --note 'Start demo and run its HTTP probe'
```

The example mounts no host data and publishes only loopback HTTP. A single HTTP 200 is the declared probe scope; it does not prove any unrelated business workflow. The metadata's `commands` are explanatory strings and are not executed by the tool.

For a fresh agent, begin with workspace `AGENTS.md`, `handoff`, the registered service detail, and its README. The optional evaluation questions in `docs/mcp-evaluation.xml` refer to a registered copy of this example under `/srv/workspace`, with the metadata below preserved.
