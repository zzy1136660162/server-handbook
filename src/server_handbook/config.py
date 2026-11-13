"""Explicit workspace configuration and loss-preventing file updates."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

from .errors import HandbookError

CONFIG_VERSION = 1
CONFIG_FIELDS = {"schema_version", "name", "workspace", "project_roots", "service_roots", "records_dir", "profile"}
BEGIN = "<!-- server-handbook:begin -->"
END = "<!-- server-handbook:end -->"


def absolute_path(value, field="path") -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip() or "\0" in str(value):
        raise HandbookError(f"{field} must be a nonempty path.")
    try:
        return Path(value).expanduser().resolve()
    except (OSError, ValueError) as error:
        raise HandbookError(f"Cannot resolve {field}; check the path and permissions.") from error


def validate_config(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != CONFIG_FIELDS:
        raise HandbookError("Config fields must match schema version 1; do not put credentials in this file.")
    if type(value["schema_version"]) is not int or value["schema_version"] != CONFIG_VERSION:
        raise HandbookError("Unsupported config schema; upgrade the tool before opening this workspace.")
    if not isinstance(value["name"], str) or not value["name"].strip() or len(value["name"]) > 200:
        raise HandbookError("name must contain 1–200 characters.")
    if value["profile"] != "linux-compose":
        raise HandbookError("This release supports the linux-compose host profile.")
    result = dict(value)
    for field in ("workspace", "records_dir"):
        if not isinstance(value[field], str) or not Path(value[field]).is_absolute():
            raise HandbookError(f"{field} must be an absolute path.")
        result[field] = str(absolute_path(value[field], field))
    workspace = Path(result["workspace"])
    if workspace == Path(workspace.anchor):
        raise HandbookError("Use a dedicated workspace directory, not a filesystem root.")
    for field in ("project_roots", "service_roots"):
        if not isinstance(value[field], list) or not value[field] or len(value[field]) > 32:
            raise HandbookError(f"{field} must contain 1–32 absolute directory paths.")
        result[field] = []
        for path in value[field]:
            if not isinstance(path, str) or not Path(path).is_absolute():
                raise HandbookError(f"{field} entries must be absolute paths.")
            resolved = absolute_path(path, field)
            if resolved == Path(resolved.anchor):
                raise HandbookError(f"{field} cannot include an entire filesystem root.")
            result[field].append(str(resolved))
    return result


def load_config(path=None) -> dict:
    selected = path or os.environ.get("SERVER_HANDBOOK_CONFIG")
    if not selected:
        current = Path.cwd().resolve()
        selected = next((parent/".server-handbook/config.json" for parent in (current, *current.parents)
                         if (parent/".server-handbook/config.json").is_file()), None)
    if not selected:
        raise HandbookError("No workspace config found. Run 'server-handbook init --workspace PATH --apply' or pass --config.")
    config_path = absolute_path(selected, "config")
    try:
        data = config_path.read_bytes()
        if len(data) > 65536:
            raise HandbookError("Config exceeds 64 KiB.")
        config = validate_config(json.loads(data.decode("utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HandbookError(f"Cannot read UTF-8 config at {config_path}; check its syntax and permissions.") from error
    config["_config_path"] = str(config_path)
    return config


def allowed_roots(config) -> list[Path]:
    return list(dict.fromkeys(absolute_path(path) for path in
                              [config["workspace"], *config["project_roots"], *config["service_roots"]]))


def contained_path(config, path, *, field="path") -> Path:
    if not isinstance(path, (str, Path)) or not Path(path).is_absolute():
        raise HandbookError(f"{field} must be an absolute path.")
    resolved = absolute_path(path, field)
    if not any(resolved.is_relative_to(root) for root in allowed_roots(config)):
        raise HandbookError(f"{field} is outside configured workspace roots, including after resolving symlinks.")
    return resolved


def atomic_write(path, data: bytes, *, expected_sha256=None, create_only=False):
    path = Path(path)
    if not isinstance(data, bytes):
        raise TypeError("atomic_write expects bytes")
    if path.is_symlink():
        raise HandbookError(f"Refusing to replace an existing symbolic link: {path}")
    try:
        before = path.read_bytes() if path.exists() else None
        if create_only and before is not None:
            raise HandbookError(f"File already exists; review it instead of replacing it: {path}")
        if expected_sha256 is not None and (before is None or hashlib.sha256(before).hexdigest() != expected_sha256):
            raise HandbookError(f"File changed since it was read; retry after reviewing: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".handbook-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if before is not None:
                os.chmod(temporary, path.stat().st_mode & 0o777)
            if create_only:
                # A hard link gives create-only semantics without an overwrite race.
                os.link(temporary, path)
                temporary.unlink()
            else:
                current = path.read_bytes() if path.exists() else None
                if current != before:
                    raise HandbookError(f"File changed during rendering; no overwrite was made: {path}")
                os.replace(temporary, path)
            if os.name != "nt":
                directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    except OSError as error:
        raise HandbookError(f"Cannot safely write {path}; check permissions and available space.") from error


def managed_text(original: str, body: str) -> str:
    block = f"{BEGIN}\n{body.rstrip()}\n{END}"
    starts, ends = original.count(BEGIN), original.count(END)
    if starts == ends == 0:
        return original + ("\n\n" if original and not original.endswith("\n\n") else "") + block + "\n"
    if starts != 1 or ends != 1 or original.index(BEGIN) > original.index(END):
        raise HandbookError("Managed document markers are malformed; repair them before rendering.")
    return original[:original.index(BEGIN)] + block + original[original.index(END)+len(END):]


def initialize(workspace, *, name="Server Handbook", project_roots=None, service_roots=None,
               records_dir=None, apply=False) -> dict:
    workspace = absolute_path(workspace, "workspace")
    records = absolute_path(records_dir) if records_dir else workspace/".server-handbook"
    config = validate_config({"schema_version": CONFIG_VERSION, "name": name, "workspace": str(workspace),
                              "project_roots": [str(absolute_path(p)) for p in (project_roots or [workspace/"projects"])],
                              "service_roots": [str(absolute_path(p)) for p in (service_roots or [workspace/"services"])],
                              "records_dir": str(records), "profile": "linux-compose"})
    config_path = workspace/".server-handbook/config.json"
    if not config_path.resolve().is_relative_to(workspace) or config_path.is_symlink():
        raise HandbookError("Workspace config must stay inside the workspace; review its symbolic links.")
    config_data = (json.dumps(config, ensure_ascii=False, indent=2)+"\n").encode("utf-8")
    config_before = config_path.read_bytes() if config_path.exists() else None
    if config_path.exists():
        existing = load_config(config_path)
        if {k: v for k, v in existing.items() if k != "_config_path"} != config:
            raise HandbookError("Workspace is already configured differently; review its config explicitly instead of reinitializing.")
    files = {config_path: config_data}
    originals = {config_path: config_before}
    bodies = {
        workspace/"AGENTS.md": f"## Server Handbook\n\nStart with `server-handbook handoff` and `server-handbook doctor`.\n"
        f"Workspace: `{workspace}`. Config: `{config_path}`.\n"
        "Read a service's registered README before editing it. Keep declarations, live observations, and dated receipts distinct.\n"
        "Use registered Compose/systemd operations for tracked changes; inspect plans before apply.\n"
        "Update the service descriptor and register with the current revision after approved metadata changes.\n"
        "Do not replay interrupted operations or first-time data initialization. Diagnose first.\n"
        "User instructions and project rules remain authoritative. Never execute descriptor commands strings.\n"
        "Managed operations record their real results; shell changes outside this tool are not automatically attributable.",
        workspace/"README.md": f"## Server Handbook entry\n\nAgent entry: [AGENTS.md](AGENTS.md).\n"
        "Run `server-handbook handoff`, `server-handbook list`, and `server-handbook doctor` here.\n"
        "Service declarations live in `<service-home>/deploy/registry/<id>.json`.\n"
        "Use `server-handbook project new --help` or `server-handbook adopt --help` to prepare a project.\n"
        "Review and register declarations; live status is queried separately. Existing paths and manual notes remain intact.",
    }
    for path, body in bodies.items():
        try:
            if path.is_symlink():
                raise HandbookError(f"Review the existing Markdown symlink manually: {path}")
            original_data = path.read_bytes() if path.exists() else None
            if original_data is not None and len(original_data) > 65536:
                raise HandbookError(f"Managed Markdown exceeds 64 KiB; split the document first: {path}")
            original = original_data.decode("utf-8") if original_data is not None else ""
        except (OSError, UnicodeDecodeError) as error:
            raise HandbookError(f"Cannot read existing Markdown at {path}; no overwrite was made.") from error
        files[path] = managed_text(original, body).encode("utf-8")
        originals[path] = original_data
    changes = [{"path": str(path), "action": "update_managed_block" if path.exists() else "create"}
               for path, data in files.items() if not path.exists() or path.read_bytes() != data]
    if apply:
        for directory in [workspace, records, *map(Path, config["project_roots"]), *map(Path, config["service_roots"])]:
            directory.mkdir(parents=True, exist_ok=True)
        from .registry import initialize_database
        initialize_database(config)
        for path, data in files.items():
            if path.exists() and path.read_bytes() == data:
                continue
            old = hashlib.sha256(originals[path]).hexdigest() if originals[path] is not None else None
            atomic_write(path, data, expected_sha256=old, create_only=old is None)
    return {"applied": apply, "config": config, "config_path": str(config_path), "changes": changes,
            "next": "Review project/adopt plans, then register a service declaration."}
