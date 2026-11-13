# Local build and publication

The public source repository is [zzy1136660162/server-handbook](https://github.com/zzy1136660162/server-handbook). PyPI publication is a separate maintainer action; a public GitHub push does not upload packages to PyPI. Follow the [exact Trusted Publishing setup](pypi-publishing.zh-CN.md) before dispatching the manual publishing workflow.

1. Install development dependencies in an isolated environment and run default tests.
2. Run the explicit isolated Docker test when validating the Compose lifecycle. Check that its unique project was removed.
3. Run `python -m build`, inspect wheel/sdist contents and run `python -m twine check dist/*.whl dist/*.tar.gz` if twine is installed. Source ZIPs and checksum text files are not PyPI distribution uploads.
4. Install the wheel in a fresh environment without extras; verify CLI commands and optional-MCP error handling. Install the MCP extra and exercise the actual stdio process.
5. Check the bundled schema and Skill. Export the Skill with the installed CLI, and verify a configured client can locate the actual server entry.
6. Record actual test environments and limitations. CI configuration in this repository has not run merely because the file exists.

The wheel contains only the application and its packaged schema/Skill assets. The sdist includes public docs, examples and tests. The release must exclude local environments, application state, operation receipts, personal inventory, SSH credentials and migration/forensic materials.

The distribution name is `server-handbook`, import name `server_handbook`, CLI `server-handbook`, and MCP CLI `server-handbook-mcp`. Version comes from `server_handbook.__version__`. Upgrade release metadata and SQLite schema deliberately; unknown existing database versions are rejected. Do not replace a user's state database to make an upgrade appear successful.

MCP Registry publication is optional after a publicly installable package exists. Its metadata describes this software's installation, not each user's private server inventory. Use the current official publisher and registry schema when that publishing step is authorized; the Registry remains in preview at the time of this project's initial development.
