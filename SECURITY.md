# Operational boundaries

The default MCP mode is read-only. Enabling write tools allows the account running the process to register metadata and execute supported actions against registered services. The account's existing operating-system permissions still apply. Do not treat MCP annotations or Markdown instructions as permission enforcement.

Use trusted local declarations. A Docker socket can confer broad host authority, and Compose files can describe powerful workloads; this tool does not sandbox those workloads. Plans must be reviewed within the operator's existing authorization.

Configuration paths and secret references are metadata only. Raw interpolated Compose configuration, raw command stderr/stdout and HTTP bodies are not retained in operation receipts. Metadata notes and manual README text remain authored inputs: do not put secret values in them.

An interrupted or timed-out action may have external effects. Inspect the target before retrying. Recovery marks a dead owner's operation interrupted; it does not roll back or replay it. Keep backups appropriate to the application; the metadata registry is not a database backup product.

For a suspected defect, send a minimal anonymous reproduction through the repository's maintainer-selected reporting channel when one is established. No reporting mailbox or hosted service is configured by this local distribution. Avoid including production credentials or private data in public issue reports.
