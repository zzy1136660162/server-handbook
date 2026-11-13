"""Validated deployment declarations and transactional local registration."""
from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

from .config import contained_path
from .errors import HandbookError

SCHEMA_VERSION = 1
MAX_DOCUMENT_BYTES = 65536
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
UNIT_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9@_.:\\\-]*\.(?:service|socket|timer|target|mount|automount|path|slice|scope)\Z")
KINDS = ("compose", "systemd", "project", "desktop", "toolchain")
STATES = ("prepared", "installed", "deployed", "retired")
LIST_FIELDS = ("systemd_units", "packages", "local_ports", "remote_endpoints", "config_paths",
               "data_paths", "secret_refs", "depends_on", "notes", "evidence", "checks")
DESCRIPTOR_FIELDS = {"schema_version", "id", "name", "kind", "state", "home", "readme",
                     "version", "compose", "commands", *LIST_FIELDS}
SERVER_FIELDS = {"revision", "recorded_at", "recorded_by", "descriptor_path", "descriptor_sha256"}
PRIVATE_PARTS = {"config", "configs", "secret", "secrets", "private", ".ssh", ".aws", ".gnupg"}


def _text(value, field, maximum=4096):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\0" in value:
        raise HandbookError(f"{field} must be nonempty text of at most {maximum} characters.")
    return value


def _service_id(value):
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise HandbookError("Service id must contain 1–64 lowercase letters, digits, underscores or hyphens, starting with a letter or digit.")
    return value


def _read_bytes(path, maximum):
    path = Path(path)
    try:
        if not path.is_file():
            raise HandbookError(f"Expected a regular file: {path}.")
        with path.open("rb") as stream:
            data = stream.read(maximum + 1)
    except OSError as error:
        raise HandbookError(f"Cannot read {path}; check its location and permissions.") from error
    if len(data) > maximum:
        raise HandbookError(f"{path} exceeds the {maximum}-byte size limit.")
    return data


def _json(data, label):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise HandbookError(f"{label} contains duplicate JSON key {key!r}.")
            result[key] = value
        return result
    try:
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        return json.loads(data, object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
        raise HandbookError(f"{label} must contain valid UTF-8 JSON.") from error


def _database_path(config):
    directory = Path(config["records_dir"]).resolve()
    path = directory / "state.sqlite3"
    if path.resolve().parent != directory:
        raise HandbookError("The state database must stay inside records_dir; remove the unexpected symlink.")
    return path


def _schema(connection):
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        raise HandbookError(f"Unsupported state database version {version}; use the matching application version or an explicit migration. The database was not replaced.")
    columns = {
        "services": {"id", "revision", "document"},
        "history": {"id", "service_id", "revision", "note", "document", "recorded_at"},
        "operations": {"id", "service_id", "status", "document"},
    }
    for table, expected in columns.items():
        # Table names here are fixed application constants, never user input.
        actual = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if actual != expected:
            raise HandbookError("The state database structure is incomplete or incompatible; restore a verified backup before continuing.")


def initialize_database(config):
    """Create only an empty state database; never reinterpret an existing database."""
    path = _database_path(config)
    connection = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=5)
        connection.execute("BEGIN IMMEDIATE")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if version == 0 and not tables:
            connection.execute("CREATE TABLE services (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, document TEXT NOT NULL)")
            connection.execute("CREATE TABLE history (id INTEGER PRIMARY KEY AUTOINCREMENT, service_id TEXT NOT NULL, revision INTEGER NOT NULL, note TEXT NOT NULL, document TEXT NOT NULL, recorded_at TEXT NOT NULL, UNIQUE(service_id,revision))")
            connection.execute("CREATE TABLE operations (id TEXT PRIMARY KEY, service_id TEXT NOT NULL, status TEXT NOT NULL, document TEXT NOT NULL)")
            connection.execute("PRAGMA user_version=1")
        _schema(connection)
        connection.commit()
    except sqlite3.Error as error:
        if connection:
            connection.rollback()
        raise HandbookError("Cannot initialize the state database; check permissions and whether another process is using it.") from error
    except HandbookError:
        if connection:
            connection.rollback()
        raise
    finally:
        if connection:
            connection.close()
    return str(path)


@contextmanager
def database(config, write=False):
    """Open existing local state; readonly queries never create files or directories."""
    path = _database_path(config)
    if not path.is_file():
        raise HandbookError("State database is missing. Run init --apply with this configuration before using the registry.")
    connection = None
    try:
        uri = path.as_uri() + ("?mode=rw" if write else "?mode=ro")
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        if write:
            connection.execute("BEGIN IMMEDIATE")
        else:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
        _schema(connection)
        yield connection
        if write:
            connection.commit()
    except sqlite3.Error as error:
        if connection:
            connection.rollback()
        raise HandbookError("Cannot access the state database. Check permissions, available space and concurrent operations, then retry.") from error
    except Exception:
        if connection:
            connection.rollback()
        raise
    finally:
        if connection:
            connection.close()


def _markdown(config, path):
    resolved = contained_path(config, path, field="document path")
    if resolved.suffix.lower() != ".md" or any(part.lower() in PRIVATE_PARTS for part in resolved.parts):
        raise HandbookError("Only public Markdown documents outside private/config/secrets directories can be read.")
    return resolved


def _read_markdown(path):
    data = _read_bytes(path, MAX_DOCUMENT_BYTES)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise HandbookError("The document must be UTF-8 Markdown.") from error
    if any(ord(c) < 32 and c not in "\n\r\t" for c in text):
        raise HandbookError("The document contains non-text content.")
    return text


def validate_descriptor(config, descriptor, *, check_files=False):
    """Validate a declaration; command strings are documentation and are never executed."""
    if not isinstance(descriptor, dict) or not all(isinstance(key, str) for key in descriptor):
        raise HandbookError("A service declaration must be a JSON object.")
    unknown = set(descriptor) - DESCRIPTOR_FIELDS
    if unknown:
        raise HandbookError(f"Unknown declaration fields: {', '.join(sorted(unknown))}.")
    if type(descriptor.get("schema_version")) is not int or descriptor["schema_version"] != 1:
        raise HandbookError("Declaration schema_version must be 1.")
    result = copy.deepcopy(descriptor)
    service_id = _service_id(result.get("id"))
    _text(result.get("name"), "name", 200)
    if result.get("kind") not in KINDS or result.get("state") not in STATES:
        raise HandbookError("Unsupported declaration kind or state.")
    home = contained_path(config, result.get("home"), field="home")
    readme = _markdown(config, result.get("readme"))
    if not readme.is_relative_to(home):
        raise HandbookError("readme must be inside the service home.")
    result["home"], result["readme"] = str(home), str(readme)
    if "version" in result:
        _text(result["version"], "version", 200)
    for field in LIST_FIELDS:
        result.setdefault(field, [])
        if not isinstance(result[field], list) or len(result[field]) > 256:
            raise HandbookError(f"{field} must be an array with at most 256 entries.")
    for field in set(LIST_FIELDS) - {"local_ports", "remote_endpoints", "checks"}:
        for value in result[field]:
            _text(value, field)
    for unit in result["systemd_units"]:
        if len(unit) > 256 or not UNIT_PATTERN.fullmatch(unit):
            raise HandbookError(f"Invalid systemd unit name: {unit}.")
    for dependency in result["depends_on"]:
        _service_id(dependency)
        if dependency == service_id:
            raise HandbookError("A service cannot depend on itself.")
    for field in ("config_paths", "data_paths", "secret_refs", "evidence"):
        for value in result[field]:
            if not Path(value).is_absolute():
                raise HandbookError(f"{field} must contain absolute path references.")
    for field, keys in (("local_ports", {"bind", "port", "transport", "purpose"}),
                        ("remote_endpoints", {"direction", "host", "port", "transport", "purpose"})):
        for endpoint in result[field]:
            if not isinstance(endpoint, dict) or set(endpoint) != keys:
                raise HandbookError(f"Each {field} entry must contain exactly {', '.join(sorted(keys))}.")
            if type(endpoint["port"]) is not int or not 1 <= endpoint["port"] <= 65535:
                raise HandbookError(f"{field}.port must be an integer from 1 to 65535.")
            if endpoint["transport"] not in ("tcp", "udp"):
                raise HandbookError(f"{field}.transport must be tcp or udp.")
            for key in keys - {"port"}:
                _text(endpoint[key], f"{field}.{key}")
            if field == "remote_endpoints" and endpoint["direction"] not in ("inbound", "outbound"):
                raise HandbookError("remote_endpoints.direction must be inbound or outbound.")
    result.setdefault("commands", {})
    if not isinstance(result["commands"], dict) or set(result["commands"]) - {"status", "start", "stop", "restart", "logs"}:
        raise HandbookError("commands only accepts status/start/stop/restart/logs; commands are display-only.")
    for value in result["commands"].values():
        _text(value, "commands")
    if "compose" in result:
        compose = result["compose"]
        if not isinstance(compose, dict) or set(compose) != {"project", "service", "files"}:
            raise HandbookError("compose must contain exactly project, service and files.")
        patterns = {"project": r"[a-z0-9][a-z0-9_-]{0,62}\Z", "service": r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z"}
        for field, pattern in patterns.items():
            if not isinstance(compose[field], str) or not re.fullmatch(pattern, compose[field]):
                raise HandbookError(f"Invalid compose.{field}; use a name, not command-line options.")
        if not isinstance(compose["files"], list) or not 1 <= len(compose["files"]) <= 32:
            raise HandbookError("compose.files must contain 1–32 file paths in load order.")
        paths = []
        for file in compose["files"]:
            path = contained_path(config, file, field="compose.files")
            if not path.is_relative_to(home):
                raise HandbookError("Compose files must stay inside the service home.")
            if check_files and not path.is_file():
                raise HandbookError(f"Compose file does not exist: {path}.")
            paths.append(str(path))
        result["compose"]["files"] = paths
    if result["state"] == "deployed":
        if result["kind"] == "compose" and "compose" not in result:
            raise HandbookError("A deployed Compose service requires its compose declaration.")
        if result["kind"] == "systemd" and not result["systemd_units"]:
            raise HandbookError("A deployed systemd service requires at least one unit.")
    if len(result["checks"]) > 16:
        raise HandbookError("At most 16 HTTP checks are supported per service.")
    for check in result["checks"]:
        if not isinstance(check, dict) or set(check) != {"type", "url", "expected_status", "timeout_seconds"} or check.get("type") != "http":
            raise HandbookError("Each check requires type=http, url, expected_status and timeout_seconds.")
        _text(check["url"], "checks.url", 2048)
        try:
            url = urlsplit(check["url"])
            valid = (url.scheme in ("http", "https") and url.hostname and url.username is None
                     and url.password is None and not any(c.isspace() or ord(c) < 32 for c in check["url"]))
            if url.port is not None and not 1 <= url.port <= 65535:
                valid = False
        except ValueError:
            valid = False
        if not valid:
            raise HandbookError("HTTP check URLs must use http/https and must not contain credentials.")
        if type(check["expected_status"]) is not int or not 100 <= check["expected_status"] <= 599:
            raise HandbookError("checks.expected_status must be an HTTP status integer from 100 to 599.")
        if type(check["timeout_seconds"]) is not int or not 1 <= check["timeout_seconds"] <= 30:
            raise HandbookError("checks.timeout_seconds must be an integer from 1 to 30.")
    if check_files:
        if not home.is_dir():
            raise HandbookError(f"Service home does not exist: {home}.")
        _read_markdown(readme)
    return result


def _validate_record(config, record):
    if not isinstance(record, dict) or set(record) - DESCRIPTOR_FIELDS - SERVER_FIELDS or not SERVER_FIELDS <= set(record):
        raise HandbookError("A registered snapshot contains missing or unknown metadata fields.")
    validate_descriptor(config, {key: value for key, value in record.items() if key not in SERVER_FIELDS})
    if type(record["revision"]) is not int or record["revision"] < 1:
        raise HandbookError("A registered revision must be a positive integer.")
    _text(record["recorded_at"], "recorded_at", 100)
    _text(record["recorded_by"], "recorded_by", 200)
    home = Path(record["home"])
    actual = contained_path(config, record["descriptor_path"], field="descriptor_path")
    expected = (home / "deploy" / "registry" / f"{record['id']}.json").resolve()
    if actual != expected or not actual.is_relative_to(home):
        raise HandbookError("descriptor_path must be <home>/deploy/registry/<id>.json and stay inside home.")
    if not isinstance(record["descriptor_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", record["descriptor_sha256"]):
        raise HandbookError("descriptor_sha256 must be a SHA-256 digest.")


def _records(connection, config):
    records = {}
    for row in connection.execute("SELECT id,revision,document FROM services ORDER BY id"):
        record = _json(row["document"], "Registered service")
        _validate_record(config, record)
        if record["id"] != row["id"] or record["revision"] != row["revision"]:
            raise HandbookError("Service row and snapshot metadata disagree; restore verified state before changing it.")
        records[row["id"]] = record
    return records


def _history_errors(connection, config, records):
    latest = {}
    for row in connection.execute("SELECT service_id,revision,note,document,recorded_at FROM history ORDER BY id"):
        snapshot = _json(row["document"], "Historical snapshot")
        _validate_record(config, snapshot)
        _text(row["note"], "history note", 2048)
        service_id = snapshot["id"]
        previous = latest.get(service_id, {}).get("revision", 0)
        if (service_id != row["service_id"] or row["revision"] != previous + 1
                or snapshot["revision"] != row["revision"] or snapshot["recorded_at"] != row["recorded_at"]):
            return ["History revision sequence or snapshot metadata is inconsistent; do not overwrite the database."]
        latest[service_id] = snapshot
    if latest != records:
        return ["Current services do not match their last historical snapshots; do not overwrite the database."]
    return []


def register_service(config, file, note, expect_revision=None):
    """Commit a validated snapshot and immutable revision history in one transaction."""
    _text(note, "note", 2048)
    if expect_revision is not None and (type(expect_revision) is not int or expect_revision < 1):
        raise HandbookError("expect_revision must be a positive integer.")
    source_path = contained_path(config, file, field="declaration file")
    source = _read_bytes(source_path, 131072)
    descriptor = validate_descriptor(config, _json(source, "Service declaration"), check_files=True)
    home = Path(descriptor["home"])
    if source_path != (home / "deploy" / "registry" / f"{descriptor['id']}.json").resolve() or not source_path.is_relative_to(home):
        raise HandbookError("Save the declaration as <home>/deploy/registry/<id>.json before registering it.")
    with database(config, write=True) as connection:
        records = _records(connection, config)
        errors = _history_errors(connection, config, records)
        if errors:
            raise HandbookError(errors[0])
        old = records.get(descriptor["id"])
        if old and expect_revision != old["revision"]:
            raise HandbookError(f"Revision conflict: {descriptor['id']} is revision {old['revision']}; reread it and supply --expect-revision {old['revision']}.")
        if not old and expect_revision is not None:
            raise HandbookError("Revision conflict: the service is not registered; omit --expect-revision for its first registration.")
        if connection.execute("SELECT 1 FROM operations WHERE service_id=? AND status='running' LIMIT 1", (descriptor["id"],)).fetchone():
            raise HandbookError("This service has a running or unfinished operation. Inspect/recover it before updating the declaration.")
        if _read_bytes(source_path, 131072) != source:
            raise HandbookError("The declaration changed while registering it; reread the file and retry.")
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        actor = f"uid:{os.getuid()}" if hasattr(os, "getuid") else f"user:{getpass.getuser()}"
        record = {**descriptor, "revision": old["revision"] + 1 if old else 1,
                  "recorded_at": stamp, "recorded_by": actor,
                  "descriptor_path": str(source_path), "descriptor_sha256": hashlib.sha256(source).hexdigest()}
        encoded = json.dumps(record, ensure_ascii=False)
        connection.execute("INSERT INTO services(id,revision,document) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,document=excluded.document", (record["id"], record["revision"], encoded))
        connection.execute("INSERT INTO history(service_id,revision,note,document,recorded_at) VALUES(?,?,?,?,?)", (record["id"], record["revision"], note, encoded, stamp))
    return record


def _last_operation(connection, service_id):
    row = connection.execute("SELECT document FROM operations WHERE service_id=? ORDER BY rowid DESC LIMIT 1", (service_id,)).fetchone()
    if row is None:
        return None
    document = _json(row["document"], "Operation receipt")
    if not isinstance(document, dict):
        raise HandbookError("An operation receipt is malformed.")
    fields = ("id", "service_id", "action", "status", "registered_revision", "started_at", "finished_at", "verification_scope")
    return {key: document[key] for key in fields if key in document}


def get_service(config, id):
    """Return the registered snapshot and latest operation summary, without executing it."""
    _service_id(id)
    with database(config) as connection:
        records = _records(connection, config)
        if id not in records:
            raise HandbookError(f"Service {id!r} is not registered; use list to find its id.")
        return {**records[id], "last_operation": _last_operation(connection, id)}


def list_services(config, offset=0, limit=20):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise HandbookError("Use offset >= 0 and limit between 1 and 100.")
    fields = ("id", "name", "kind", "state", "home", "readme", "version", "revision", "recorded_at", "depends_on", "local_ports", "systemd_units")
    with database(config) as connection:
        records = list(_records(connection, config).values())
    services = [{key: record[key] for key in fields if key in record} for record in records[offset:offset + limit]]
    end = offset + limit
    return {"services": services, "total": len(records), "next_offset": end if end < len(records) else None}


def service_history(config, id):
    _service_id(id)
    with database(config) as connection:
        if id not in _records(connection, config):
            raise HandbookError(f"Service {id!r} is not registered.")
        rows = connection.execute("SELECT revision,note,document,recorded_at FROM history WHERE service_id=? ORDER BY revision", (id,)).fetchall()
        return [{"service_id": id, "revision": row["revision"], "note": row["note"],
                 "recorded_at": row["recorded_at"], "record": _json(row["document"], "Historical snapshot")} for row in rows]


def validate_registry(config, check_files=True):
    """Report declaration drift and history damage without altering files or state."""
    try:
        with database(config) as connection:
            records = _records(connection, config)
            errors = _history_errors(connection, config, records)
        for service_id, record in records.items():
            for dependency in record["depends_on"]:
                if dependency not in records:
                    errors.append(f"{service_id}: dependency {dependency!r} is not registered.")
            if check_files:
                try:
                    validate_descriptor(config, {key: value for key, value in record.items() if key not in SERVER_FIELDS}, check_files=True)
                    source = _read_bytes(record["descriptor_path"], 131072)
                    if hashlib.sha256(source).hexdigest() != record["descriptor_sha256"]:
                        errors.append(f"{service_id}: declaration SHA-256 differs from its registered revision; review and register the change.")
                except HandbookError as error:
                    errors.append(f"{service_id}: {error}")
        return errors
    except HandbookError as error:
        return [str(error)]


def read_document(config, path):
    candidate = _markdown(config, path)
    workspace = Path(config["workspace"])
    allowed = {(workspace / name).resolve() for name in ("AGENTS.md", "README.md")}
    secrets = set()
    with database(config) as connection:
        for record in _records(connection, config).values():
            allowed.add(_markdown(config, record["readme"]))
            secrets.update(Path(secret).resolve() for secret in record["secret_refs"])
    if candidate not in allowed or any(candidate == secret or secret in candidate.parents for secret in secrets):
        raise HandbookError("Only workspace entry documents and registered public READMEs are readable; configuration and secret files are excluded.")
    return {"path": str(candidate), "text": _read_markdown(candidate)}


def descriptor_template(config, id, home, name, kind):
    return validate_descriptor(config, {"schema_version": 1, "id": id, "name": name,
                                       "kind": kind, "state": "prepared", "home": str(home),
                                       "readme": str(Path(home) / "README.md")})
