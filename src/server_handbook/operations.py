"""Fixed service actions with durable receipts; descriptor commands are never run."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .config import contained_path
from .errors import HandbookError
from .registry import database, get_service, validate_descriptor

COMMAND_TIMEOUT = 60
PROBE_TIMEOUT = 5
MAX_OUTPUT_BYTES = 65536
VERIFY_ATTEMPTS = 10
VERIFY_INTERVAL = 2


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _note(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2048 or "\0" in value:
        raise HandbookError("A nonempty note of at most 2048 characters is required.")
    return value


def _fingerprint(path):
    try:
        with Path(path).open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError as error:
        raise HandbookError("A declared deployment file cannot be read; check paths and permissions.") from error


def _service_plan(config, service_id, action):
    record = get_service(config, service_id)
    source = contained_path(config, record["descriptor_path"], field="descriptor")
    try:
        with source.open("rb") as stream:
            data = stream.read(131073)
        if len(data) > 131072 or hashlib.sha256(data).hexdigest() != record["descriptor_sha256"]:
            raise HandbookError("Deployment declaration changed; review and register it before applying.")
        descriptor = validate_descriptor(config, json.loads(data.decode("utf-8")), check_files=True)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HandbookError("Cannot read the registered deployment declaration; review it before applying.") from error
    if descriptor["state"] == "retired" and action != "stop":
        raise HandbookError("A retired service can only be stopped; review its declaration before starting it.")
    commands, preflight = [], []
    input_hashes = {str(source): record["descriptor_sha256"]}
    if descriptor["kind"] == "compose":
        if action not in ("up", "restart", "stop") or "compose" not in descriptor:
            raise HandbookError("Compose supports up, restart and stop with a registered compose declaration.")
        compose = descriptor["compose"]
        prefix = ["docker", "compose", "--project-name", compose["project"]]
        home = Path(descriptor["home"]).resolve()
        for file in compose["files"]:
            resolved = contained_path(config, file, field="compose file")
            if not resolved.is_relative_to(home):
                raise HandbookError("Compose files must remain inside the registered service directory.")
            prefix += ["-f", str(resolved)]
            input_hashes[str(resolved)] = _fingerprint(resolved)
        preflight = [prefix + ["config", "--quiet"]]
        # shortcut: dependency services must already exist; add dependency planning before managing whole stacks.
        arguments = {"up": ["up", "--detach", "--no-deps"],
                     "restart": ["restart", "--no-deps"], "stop": ["stop"]}[action]
        commands = [prefix + arguments + [compose["service"]]]
        probes = [prefix + ["ps", "--all", "--format", "json", compose["service"]]]
    elif descriptor["kind"] == "systemd":
        units = descriptor["systemd_units"]
        if action not in ("start", "restart", "stop", "reload") or not units:
            raise HandbookError("systemd supports start, restart, stop and reload for registered units.")
        commands = [["systemctl", "--no-ask-password", action, "--", *units]]
        probes = [["systemctl", "show", "--no-pager", "--property=Id,LoadState,ActiveState,SubState", "--", unit]
                  for unit in units]
    else:
        raise HandbookError("Managed actions support compose and systemd services only.")
    http_checks = descriptor.get("checks", []) if action in ("up", "start", "restart") else []
    return descriptor, {"service_id": service_id, "action": action,
                        "registered_revision": record["revision"], "cwd": descriptor["home"],
                        "preflight": preflight, "commands": commands, "probes": probes,
                        "input_hashes": input_hashes,
                        "http_checks": [{**check, "url": _public_url(check["url"])} for check in http_checks],
                        "verification_scope": "process_and_configured_http" if http_checks else "process_only",
                        "warnings": ["Only the named Compose service is managed; dependencies must already be running."]
                        if descriptor["kind"] == "compose" else []}


def plan_operation(config, service_id, action) -> dict:
    """Build a validated plan without running service commands or writing state."""
    return _service_plan(config, service_id, action)[1]


def _save(config, receipt):
    with database(config, write=True) as connection:
        row = connection.execute("SELECT status FROM operations WHERE id = ?", (receipt["id"],)).fetchone()
        if row is None or (row["status"] != "running" and row["status"] != receipt["status"]):
            raise HandbookError("Operation state changed; inspect its current receipt before continuing.")
        connection.execute("UPDATE operations SET status = ?, document = ? WHERE id = ?",
                           (receipt["status"], json.dumps(receipt, ensure_ascii=False), receipt["id"]))


def _inputs_unchanged(config, plan):
    for file, expected in plan["input_hashes"].items():
        resolved = contained_path(config, file, field="deployment input")
        if str(resolved) != file or _fingerprint(resolved) != expected:
            raise HandbookError("A deployment input changed during the operation; inspect the service before retrying.")


def _run(argv, cwd, timeout=COMMAND_TIMEOUT):
    started = time.monotonic()
    result = {"argv": argv, "returncode": None, "status": "failed", "stdout": ""}
    try:
        # Never persist raw command output: Compose errors can include interpolated secrets.
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            process = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=output,
                                     stderr=errors, timeout=timeout, check=False)
            result["returncode"] = process.returncode
            output.seek(0)
            raw = output.read(MAX_OUTPUT_BYTES + 1)
        if len(raw) > MAX_OUTPUT_BYTES:
            result["error"] = "Command output exceeded 64 KiB; inspect the target directly."
        else:
            result["stdout"] = raw.decode("utf-8", "replace")
            result["status"] = "succeeded" if process.returncode == 0 else "failed"
            if process.returncode:
                result["error"] = "Command failed; inspect the target's logs. Raw output was withheld."
    except subprocess.TimeoutExpired:
        result["error"] = "Command timed out; external effects may continue. Inspect the service before retrying."
    except OSError:
        result["error"] = "Command unavailable or permission denied; check the installed runtime and account."
    result["duration_ms"] = round((time.monotonic() - started) * 1000)
    return result


def _step(config, receipt, phase, argv, timeout=COMMAND_TIMEOUT):
    result = _run(argv, receipt["plan"]["cwd"], timeout)
    receipt["steps"].append({"phase": phase, **{key: value for key, value in result.items() if key != "stdout"}})
    _save(config, receipt)
    return result


def _compose_state(output, descriptor, stopping):
    try:
        value = json.loads(output) if output.strip().startswith("[") else [json.loads(line) for line in output.splitlines() if line.strip()]
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            raise ValueError
        compose = descriptor["compose"]
        if any(row.get("Service") != compose["service"] or row.get("Project", compose["project"]) != compose["project"] for row in value):
            raise ValueError
        states = [{"state": row.get("State"), "health": row.get("Health", "")} for row in value]
        passed = (all(item["state"] in ("exited", "dead", "created") for item in states) if stopping else
                  bool(states) and all(item["state"] == "running" and item["health"] in ("", "healthy") for item in states))
        return {"type": "compose_state", "passed": passed, "containers": states}
    except (ValueError, TypeError):
        return {"type": "compose_state", "passed": False, "error": "Runtime returned unrecognized service state."}


def _systemd_state(output, unit, stopping):
    properties = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    passed = properties.get("LoadState") == "loaded" and properties.get("ActiveState") in (
        ("inactive", "failed") if stopping else ("active",))
    return {"type": "systemd_state", "unit": unit, "passed": passed,
            "load_state": properties.get("LoadState", "unknown"),
            "active_state": properties.get("ActiveState", "unknown"),
            "sub_state": properties.get("SubState", "unknown")}


def _public_url(url):
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def _http_probe(check):
    result = {"type": "http", "url": _public_url(check["url"]),
              "expected_status": check["expected_status"], "actual_status": None, "passed": False}
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        with opener.open(check["url"], timeout=check["timeout_seconds"]) as response:
            result["actual_status"] = response.status
    except urllib.error.HTTPError as error:
        result["actual_status"] = error.code
        error.close()
    except (OSError, ValueError):
        result["error"] = "HTTP probe failed; verify the endpoint, certificate and timeout."
    result["passed"] = result["actual_status"] == check["expected_status"]
    return result


def _verify(config, receipt, descriptor):
    plan = receipt["plan"]
    for attempt in range(1, VERIFY_ATTEMPTS + 1):
        checks = []
        for index, argv in enumerate(plan["probes"]):
            result = _step(config, receipt, "verify_process", argv, PROBE_TIMEOUT)
            if result["status"] != "succeeded":
                checks.append({"type": "process", "passed": False, "error": result.get("error", "Probe failed.")})
            elif descriptor["kind"] == "compose":
                checks.append(_compose_state(result["stdout"], descriptor, receipt["action"] == "stop"))
            else:
                checks.append(_systemd_state(result["stdout"], descriptor["systemd_units"][index], receipt["action"] == "stop"))
        process_passed = all(check["passed"] for check in checks)
        if process_passed and receipt["action"] in ("up", "start", "restart"):
            checks += [_http_probe(check) for check in descriptor.get("checks", [])]
        receipt["checks"].extend({**check, "attempt": attempt} for check in checks)
        receipt["process_verified"] = process_passed
        receipt["business_verified"] = (all(check["passed"] for check in checks if check["type"] == "http")
                                        if process_passed and plan["http_checks"] else None)
        _save(config, receipt)
        if process_passed and all(check["passed"] for check in checks):
            return True
        if attempt < VERIFY_ATTEMPTS:
            time.sleep(VERIFY_INTERVAL)
    return False


def _render_docs(config, service_id):
    from .documents import render_service_docs
    render_service_docs(config, service_id, apply=True)


def _refresh_docs(config, receipt):
    try:
        _render_docs(config, receipt["service_id"])
        receipt["docs_status"] = "updated"
    except Exception:
        receipt["docs_status"] = "failed"
        receipt["docs_error"] = "Receipt saved, but documentation refresh failed. Run docs render after reviewing the file."
    _save(config, receipt)


def apply_operation(config, service_id, action, expect_revision, note) -> dict:
    """Apply only registered service actions and retain real outcomes, including failures."""
    note = _note(note)
    if type(expect_revision) is not int or expect_revision < 1:
        raise HandbookError("A positive --expect-revision is required; inspect the current service record first.")
    descriptor, plan = _service_plan(config, service_id, action)
    if plan["registered_revision"] != expect_revision:
        raise HandbookError("Service revision changed; inspect the current service record and rebuild the plan.")
    receipt = {"id": uuid.uuid4().hex, "service_id": service_id, "action": action, "status": "running",
               "registered_revision": expect_revision, "note": note, "started_at": _now(), "finished_at": None,
               "pid": os.getpid(), "hostname": socket.gethostname(), "boot_id": _boot_id(),
               "plan": plan, "steps": [], "checks": [], "verification_scope": plan["verification_scope"],
               "process_verified": False, "business_verified": None, "docs_status": "pending"}
    with database(config, write=True) as connection:
        current = connection.execute("SELECT revision FROM services WHERE id = ?", (service_id,)).fetchone()
        if current is None or current["revision"] != expect_revision:
            raise HandbookError("Service revision changed before execution; inspect the current record.")
        if connection.execute("SELECT id FROM operations WHERE service_id = ? AND status = 'running'", (service_id,)).fetchone():
            raise HandbookError("This service has an unfinished operation; inspect or recover it before retrying.")
        _inputs_unchanged(config, plan)
        connection.execute("INSERT INTO operations(id, service_id, status, document) VALUES (?, ?, ?, ?)",
                           (receipt["id"], service_id, "running", json.dumps(receipt, ensure_ascii=False)))
    try:
        for phase in ("preflight", "commands"):
            for argv in plan[phase]:
                _inputs_unchanged(config, plan)
                result = _step(config, receipt, phase, argv)
                if result["status"] != "succeeded":
                    receipt["error"] = result.get("error", "Service command failed.")
                    receipt["status"] = "failed"
                    break
            if receipt["status"] == "failed":
                break
        if receipt["status"] == "running":
            verified = _verify(config, receipt, descriptor)
            _inputs_unchanged(config, plan)
            receipt["status"] = "succeeded" if verified else "failed"
            if not verified:
                receipt["error"] = "Verification did not pass. Inspect the checks and service before retrying."
    except HandbookError as error:
        receipt["status"], receipt["error"] = "failed", str(error)
    except Exception:
        receipt["status"], receipt["error"] = "failed", "Unexpected execution failure; inspect the receipt and target before retrying."
    except BaseException:
        receipt["interruption"] = "Execution interrupted; reconcile the service after its operation owner exits."
        _save(config, receipt)
        raise
    receipt["finished_at"] = _now()
    _save(config, receipt)
    _refresh_docs(config, receipt)
    return receipt


def list_operations(config, service_id=None, limit=20) -> list[dict]:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise HandbookError("Operation limit must be between 1 and 100.")
    with database(config) as connection:
        if service_id is None:
            rows = connection.execute("SELECT document FROM operations ORDER BY rowid DESC LIMIT ?", (limit,))
        else:
            get_service(config, service_id)
            rows = connection.execute("SELECT document FROM operations WHERE service_id = ? ORDER BY rowid DESC LIMIT ?", (service_id, limit))
        fields = ("id", "service_id", "action", "status", "registered_revision", "note", "started_at",
                  "finished_at", "verification_scope", "process_verified", "business_verified", "docs_status")
        return [{key: value for key, value in json.loads(row["document"]).items() if key in fields} for row in rows]


def get_operation(config, operation_id) -> dict:
    if not isinstance(operation_id, str) or len(operation_id) != 32 or any(c not in "0123456789abcdef" for c in operation_id):
        raise HandbookError("Operation ID must be the 32-character identifier returned by apply.")
    with database(config) as connection:
        row = connection.execute("SELECT document FROM operations WHERE id = ?", (operation_id,)).fetchone()
    if row is None:
        raise HandbookError("Operation not found; list operations to find its identifier.")
    return json.loads(row["document"])


def _boot_id():
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except OSError:
        return None


def _owner_running(receipt):
    if receipt["hostname"] != socket.gethostname():
        raise HandbookError("Cannot verify an operation owner on another host; reconcile it on that host.")
    if receipt.get("boot_id") and _boot_id() and receipt["boot_id"] != _boot_id():
        return False
    pid = receipt["pid"]
    if type(pid) is not int or pid <= 0:
        raise HandbookError("Operation owner information is invalid; inspect the record manually.")
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:
                return False
            raise HandbookError("Cannot verify the operation owner; permission denied.")
        try:
            result = kernel.WaitForSingleObject(handle, 0)
            if result not in (0, 258):
                raise HandbookError("Cannot determine whether the operation owner is running.")
            return result == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError as error:
        raise HandbookError("Cannot verify the operation owner; permission denied.") from error


def recover_operation(config, operation_id, note) -> dict:
    """Reconcile a dead owner's receipt only; never replay or roll back commands."""
    note = _note(note)
    receipt = get_operation(config, operation_id)
    if receipt["status"] != "running":
        raise HandbookError("Only unfinished running operations need recovery.")
    if _owner_running(receipt):
        raise HandbookError("Operation owner is still running; recovery cannot interrupt an active deployment.")
    receipt.update(status="interrupted", finished_at=_now(), recovered_at=_now(), recovery_note=note,
                   recovery_warning="No command was replayed or rolled back. Inspect actual effects before a new operation.")
    _save(config, receipt)
    _refresh_docs(config, receipt)
    return receipt
