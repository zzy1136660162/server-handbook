"""Bounded, read-only discovery; observed candidates never become declarations."""
from __future__ import annotations

import itertools
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
from datetime import datetime, timezone

from .registry import database, list_services, validate_registry

DOCKER_FORMAT = ('{"id":{{json .ID}},"name":{{json .Names}},"image":{{json .Image}},'
                 '"status":{{json .Status}},"ports":{{json .Ports}},'
                 '"compose_project":{{json (.Label "com.docker.compose.project")}},'
                 '"compose_service":{{json (.Label "com.docker.compose.service")}}}')


def _probe(argv):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5, check=False)
        if result.returncode != 0 or len(result.stdout) > 524288:
            return {"status": "unknown", "reason": "Unavailable, insufficient permissions, or output exceeded the query limit."}
        return {"status": "ok", "output": result.stdout.strip()}
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "unknown", "reason": "Command unavailable or query timed out."}


def _all_services(config):
    result, offset = [], 0
    while True:
        page = list_services(config, offset=offset, limit=50)
        result.extend(page["services"])
        if page["next_offset"] is None:
            return result
        offset = page["next_offset"]


def overview(config, include_runtime=False) -> dict:
    services = list_services(config, limit=20)
    result = {"name": config["name"], "workspace": config["workspace"], "project_roots": config["project_roots"],
              "service_roots": config["service_roots"], "records_dir": config["records_dir"],
              "agent_entry": str(Path(config["workspace"])/"AGENTS.md"), "config_path": config.get("_config_path"),
              "registry": services, "note": "Declared metadata is not live health; timestamps belong to observations and receipts."}
    if not include_runtime:
        return result
    runtime = {"observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "hostname": socket.gethostname(),
               "system": platform.system(), "kernel": platform.release(), "systemd": {}}
    docker = _probe(["docker", "ps", "--all", "--format", DOCKER_FORMAT])
    if docker["status"] == "ok":
        try:
            docker = {"status": "ok", "items": [json.loads(line) for line in docker["output"].splitlines() if line.strip()]}
        except (ValueError, TypeError):
            docker = {"status": "unknown", "reason": "Unrecognized Docker output."}
    runtime["docker"] = docker
    if platform.system() == "Linux":
        addresses = _probe(["ip", "-json", "address", "show"])
        if addresses["status"] == "ok":
            try:
                addresses = {"status": "ok", "items": [{"interface": row["ifname"],
                    "addresses": [a["local"] for a in row.get("addr_info", []) if "local" in a]}
                    for row in json.loads(addresses["output"])]}
            except (ValueError, KeyError, TypeError):
                addresses = {"status": "unknown", "reason": "Unrecognized address output."}
        runtime["addresses"] = addresses
        units = sorted({unit for service in _all_services(config) for unit in service.get("systemd_units", [])})
        for unit in units:
            queried = _probe(["systemctl", "show", "--no-pager", "--property=LoadState,ActiveState,SubState,UnitFileState", "--", unit])
            if queried["status"] == "ok":
                props = dict(line.split("=", 1) for line in queried["output"].splitlines() if "=" in line)
                queried = {"status": "ok", **props} if all(k in props for k in ("LoadState", "ActiveState", "SubState")) else {
                    "status": "unknown", "reason": "Incomplete systemd response."}
            runtime["systemd"][unit] = queried
    else:
        runtime["addresses"] = {"status": "unknown", "reason": "Host probes target Linux; registry and clients are portable."}
    result["runtime"] = runtime
    return result


def discover(config) -> dict:
    candidates, warnings = [], []
    names = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")
    for root in dict.fromkeys([*config["project_roots"], *config["service_roots"]]):
        directory = Path(root)
        if not directory.is_dir():
            warnings.append(f"Configured root is not available: {directory}")
            continue
        try:
            with os.scandir(directory) as stream:
                entries = list(itertools.islice(stream, 101))
            if len(entries) > 100:
                warnings.append(f"Only the first 100 entries were considered in {directory}; adopt other paths explicitly.")
            homes = [directory, *(Path(entry.path) for entry in entries[:100] if entry.is_dir(follow_symlinks=False))]
            for home in homes:
                files = [str(file) for folder in (home, home/"deploy") for name in names
                         if (file := folder/name).is_file() and not file.is_symlink()]
                if files:
                    candidates.append({"kind": "compose", "home": str(home), "compose_files": files,
                                       "state": "unconfirmed", "source": "known compose filename; contents not read"})
        except OSError:
            warnings.append(f"Cannot inspect configured root: {directory}")
    docker = _probe(["docker", "ps", "--all", "--format", DOCKER_FORMAT])
    if docker["status"] == "ok":
        try:
            docker = {"status": "ok", "candidates": [json.loads(line) for line in docker["output"].splitlines() if line.strip()]}
        except ValueError:
            docker = {"status": "unknown", "reason": "Unrecognized Docker output."}
    return {"candidates": candidates, "docker": docker, "warnings": warnings, "registered": False,
            "note": "Discovery is read-only and does not establish purpose, ownership, backup strategy, or deployment success."}


def doctor(config, include_runtime=False) -> dict:
    errors = validate_registry(config, check_files=True)
    warnings = []
    for path in [config["workspace"], *config["project_roots"], *config["service_roots"]]:
        if not Path(path).is_dir():
            warnings.append(f"Configured directory is unavailable: {path}")
    with database(config) as connection:
        pending = [dict(row) for row in connection.execute("SELECT id,service_id,status FROM operations WHERE status IN ('running','planned') ORDER BY rowid DESC LIMIT 50")]
        docs = connection.execute("SELECT document FROM operations ORDER BY rowid DESC LIMIT 100").fetchall()
    for row in pending:
        warnings.append(f"Unfinished operation {row['id']} for {row['service_id']}; inspect and recover before retrying.")
    for row in docs:
        record = json.loads(row["document"])
        if record.get("docs_status") == "failed":
            warnings.append(f"Operation {record['id']} completed with a documentation update failure; render docs after review.")
    result = {"valid": not errors, "errors": errors, "warnings": warnings, "unfinished_operations": pending,
              "scope": "Registry integrity, source hashes and document availability; not a full business acceptance test."}
    if include_runtime:
        result["runtime"] = overview(config, include_runtime=True)["runtime"]
    return result
