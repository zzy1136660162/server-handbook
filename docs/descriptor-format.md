# Service declaration format

服务声明描述“这个服务如何部署和维护”。它与当前运行状态、某次操作的验证凭条分别保存。`deployed` 是部署声明，不证明服务此刻健康。

The editable source lives at `<service-home>/deploy/registry/<service-id>.json`. Register it through the CLI or explicitly enabled MCP registration tool. The state database stores the last registered snapshot and revision history; do not edit that database to change a declaration.

## Required fields

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer `1`; unsupported versions are rejected |
| `id` | Stable 1–64-character lowercase ID; letters, digits, `_` and `-`, starting with a letter or digit |
| `name` | Human-readable nonblank name, at most 200 characters |
| `kind` | `compose`, `systemd`, `project`, `desktop` or `toolchain` |
| `state` | `prepared`, `installed`, `deployed` or `retired` |
| `home` | Native absolute service directory inside a configured allowed root |
| `readme` | Public Markdown document inside `home`; normally `README.md` |

`prepared` means declaration/setup materials are prepared; `installed` means installed components; `deployed` declares deployment; `retired` preserves a service's historical entry. A stopped service is not automatically retired. Retirement does not delete files or stop services. Health is queried separately.

## Optional fields

| Field | Shape and constraints |
| --- | --- |
| `version` | Nonblank text, at most 200 characters |
| `systemd_units` | Unit-name array, at most 256 entries, each at most 256 characters; names cannot begin with options |
| `compose` | Exactly `project`, `service`, `files`; 1–32 absolute Compose file paths in load order, inside `home` |
| `packages` | Package-name text array |
| `local_ports` | Objects with exactly `bind`, `port`, `transport`, `purpose` |
| `remote_endpoints` | Objects with exactly `direction`, `host`, `port`, `transport`, `purpose` |
| `config_paths`, `data_paths` | Absolute path references; referenced contents are not automatically read |
| `secret_refs` | Absolute paths only; do not put credential values here |
| `depends_on` | Other stable service IDs; self-dependencies are rejected, missing registrations are reported by doctor |
| `commands` | Optional `status`, `start`, `stop`, `restart`, `logs` strings, **for display only** |
| `notes` | Nonblank text array for decisions, verification scope, and limitations |
| `evidence` | Absolute paths to dated verification materials; not a live health claim |
| `checks` | At most 16 explicitly configured HTTP checks |

Text-array fields accept at most 256 entries, each at most 4096 characters unless otherwise stated. Text cannot be blank or contain NUL. Ports are integer values from 1 to 65535; `transport` is `tcp` or `udp`. Remote `direction` is `inbound` or `outbound`. IPv4, IPv6 and loopback bindings should be recorded individually when they differ.

A deployed Compose declaration requires `compose`. A deployed systemd declaration requires at least one `systemd_units` entry. A prepared entry may omit these until configuration is reviewed.

Each HTTP check contains exactly:

```json
{
  "type": "http",
  "url": "http://127.0.0.1:18080/health",
  "expected_status": 200,
  "timeout_seconds": 5
}
```

The URL must use HTTP or HTTPS, include a hostname, contain no credentials or whitespace, and use a valid port if one is present. The expected status is an integer from 100 to 599; timeout is an integer from 1 to 30 seconds. These are real configured probes. Without a suitable application probe, process-state verification does not prove business functionality. Managed stop operations check the stopped state rather than expecting an HTTP success response.

## Anonymous example

This example uses Linux paths and a fictional local service. Replace paths and configuration with the actual reviewed deployment; the example itself is not an installed or verified service.

```json
{
  "schema_version": 1,
  "id": "example-api",
  "name": "Example API",
  "kind": "compose",
  "state": "prepared",
  "home": "/srv/workspace/projects/example-api",
  "readme": "/srv/workspace/projects/example-api/README.md",
  "compose": {
    "project": "example-api",
    "service": "api",
    "files": ["/srv/workspace/projects/example-api/deploy/compose.yaml"]
  },
  "local_ports": [
    {"bind": "127.0.0.1", "port": 18080, "transport": "tcp", "purpose": "Local API endpoint"}
  ],
  "data_paths": ["/srv/workspace/projects/example-api/data"],
  "secret_refs": ["/srv/workspace/projects/example-api/config/app.env"],
  "checks": [
    {"type": "http", "url": "http://127.0.0.1:18080/health", "expected_status": 200, "timeout_seconds": 5}
  ],
  "notes": ["Prepared example only; deployment and business behavior remain unverified."]
}
```

## Registration and validation

Use the actual native paths on the host. First registration has no expected revision:

```bash
server-handbook register /srv/workspace/projects/example-api/deploy/registry/example-api.json \
  --note 'Record the reviewed declaration and actual verification limits'
server-handbook show example-api
server-handbook doctor
```

Before updating, read the current revision and provide `--expect-revision <current-revision>`. CLI/MCP clients must not supply server-maintained `revision`, `recorded_at`, `recorded_by`, `descriptor_path`, `descriptor_sha256` or `last_operation` as source fields. Unknown fields and duplicate JSON keys are rejected. Use `notes` for explanatory text; do not copy a full `show` response over the source file.

## JSON Schema and runtime checks

The packaged Draft 2020-12 schema is `server_handbook/assets/schemas/service.schema.json`, with ID `urn:server-handbook:service:1`. An editor may associate it externally with `deploy/registry/*.json`. Do not add `$schema` to the declaration itself: it is not a declaration field in version 1.

The JSON Schema validates portable structure and limits without touching the server. Its path syntax accepts POSIX, Windows drive and UNC absolute forms. **The runtime uses the host's native path semantics**, resolves symbolic links, enforces configured roots and `readme`/Compose-file containment, and checks the exact declaration source location. A Windows path can pass portable structural validation and still be invalid on a Linux host, and vice versa.

Registration additionally checks the home directory, approved Markdown size/encoding and Compose file existence. Runtime validation rejects self-dependencies, URL authority/port problems and Python-loaded noninteger numeric representations even where a generic JSON Schema implementation is more permissive. `doctor` detects source-hash drift, missing dependencies, broken history and unfinished operation receipts. JSON Schema alone is not deployment authorization or proof of successful operation.
