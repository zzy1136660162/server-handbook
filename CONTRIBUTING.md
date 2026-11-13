# Contributing

Issues and pull requests are welcome. Public visitors have read access and can propose changes from a fork; they cannot push to this repository or merge their own proposals. The repository owner reviews and decides what enters `main`. Write access is granted only through an explicit maintainer invitation. The MIT license also permits modifying and distributing a separate copy; repository access controls protect this original repository.

欢迎提交 Issue 或通过 Fork 提交 PR。普通访客不能直接推送、合并或修改本仓库；是否采纳由维护者决定。MIT 开源允许修改自己的副本，并不意味着取得原仓库写入权限。

Use Python 3.11 or newer. Create an isolated environment, install `.[mcp,dev]`, run `python -m pytest` and `python -m build`. CLI logic belongs in the shared core; MCP and CLI must not develop different registration or execution rules.

Start changes with the user task and the observable result. Use temporary workspaces and anonymous fixtures. Do not copy a real server's credentials, inventory, application data, user documents, or operation transcripts into the repository.

Keep declarations, observations and receipts distinct. Add meaningful tests for failed writes, stale revisions, interruptions and pre-existing manual content when those behaviors change. Unknown database versions require an explicit migration design; never silently recreate state.

Default tests must not affect the developer's Docker or system services. The explicit optional Docker test uses its own unique project with no persistent mounts and must clean up only that project. Report real and simulated execution separately.

Generated documentation must preserve user-authored content outside managed markers. Do not add arbitrary shell MCP tools, automatic data resets, secret-returning document tools or generic root daemons as shortcuts.

Before a release, inspect both wheel and sdist, install the wheel in a fresh environment, exercise stdio MCP, and verify bundled schema/Skill assets. Public publishing is a maintainer action with the chosen account and package name; local build does not publish anything.
