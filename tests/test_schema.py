"""Check the published schema against anonymous fixtures and runtime rules."""

import copy
from importlib.resources import files
import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema", reason="Install the dev extra to validate published JSON Schema")

from server_handbook.config import initialize, load_config
from server_handbook.errors import HandbookError
from server_handbook import registry

SCHEMA = json.loads(files("server_handbook").joinpath("assets/schemas/service.schema.json").read_text(encoding="utf-8"))
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA, format_checker=jsonschema.FormatChecker())


@pytest.fixture
def declaration(tmp_path):
    initialized = initialize(tmp_path/"workspace", apply=True)
    config = load_config(initialized["config_path"])
    home = Path(config["workspace"])/"services/example-api"
    home.mkdir(parents=True)
    (home/"README.md").write_text("# Anonymous API fixture\n", encoding="utf-8")
    (home/"compose.yaml").write_text("services: {}\n", encoding="utf-8")
    document = {
        "schema_version": 1, "id": "example-api", "name": "Anonymous API",
        "kind": "project", "state": "prepared", "home": str(home),
        "readme": str(home/"README.md"),
    }
    return config, document


def test_schema_is_valid_and_matches_runtime_field_sets():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    assert set(SCHEMA["properties"]) == registry.DESCRIPTOR_FIELDS
    assert tuple(SCHEMA["properties"]["kind"]["enum"]) == registry.KINDS
    assert tuple(SCHEMA["properties"]["state"]["enum"]) == registry.STATES


@pytest.mark.parametrize("kind,state", [
    ("project", "prepared"), ("compose", "prepared"), ("systemd", "installed"),
    ("desktop", "installed"), ("toolchain", "installed"), ("systemd", "retired"),
])
def test_valid_minimal_declarations_match_runtime(declaration, kind, state):
    config, document = declaration
    document.update(kind=kind, state=state)
    VALIDATOR.validate(document)
    registry.validate_descriptor(config, document, check_files=True)


def test_full_compose_and_systemd_declarations_match_runtime(declaration):
    config, document = declaration
    document.update(
        kind="compose", state="deployed", version="1.0.0",
        compose={"project": "example-api", "service": "api", "files": [str(Path(document["home"])/"compose.yaml")]},
        local_ports=[{"bind": "127.0.0.1", "port": 18080, "transport": "tcp", "purpose": "API"}],
        remote_endpoints=[{"direction": "outbound", "host": "example.com", "port": 443, "transport": "tcp", "purpose": "Example dependency"}],
        config_paths=[str(Path(document["home"])/"config")], data_paths=[str(Path(document["home"])/"data")],
        secret_refs=[str(Path(document["home"])/"config/app.env")], packages=["example-package"],
        depends_on=["example-db"], commands={"status": "Display only: check the service"},
        notes=["Anonymous fixture only."], evidence=[str(Path(document["home"])/"docs/check.json")],
        checks=[{"type": "http", "url": "http://127.0.0.1:18080/health", "expected_status": 200, "timeout_seconds": 5}],
    )
    VALIDATOR.validate(document)
    registry.validate_descriptor(config, document, check_files=True)
    document.update(kind="systemd", systemd_units=["example-api.service"])
    document.pop("compose")
    VALIDATOR.validate(document)
    registry.validate_descriptor(config, document, check_files=True)


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("schema_version", 2), ("id", "--option"),
    ("id", "UPPERCASE"), ("id", "example-api\n"), ("id", "a"*65),
    ("name", " \n\t"), ("name", "a"*201), ("kind", "kubernetes"),
    ("state", "healthy"), ("revision", 1), ("commands", {"execute": "never execute this"}),
    ("home", "relative/home"), ("readme", "relative/README.md"),
    ("data_paths", ["relative/data"]), ("notes", ["\0"]),
    ("depends_on", ["--option"]), ("systemd_units", ["--bad.service"]),
    ("checks", [{"type": "shell", "command": "not supported"}]),
])
def test_invalid_fields_are_rejected_by_schema_and_runtime(declaration, field, value):
    config, document = declaration
    document[field] = value
    assert list(VALIDATOR.iter_errors(document))
    with pytest.raises(HandbookError):
        registry.validate_descriptor(config, document)


@pytest.mark.parametrize("port", [0, 65536, True, "18080"])
def test_port_boundaries_match_runtime(declaration, port):
    config, document = declaration
    document["local_ports"] = [{"bind": "127.0.0.1", "port": port, "transport": "tcp", "purpose": "API"}]
    assert list(VALIDATOR.iter_errors(document))
    with pytest.raises(HandbookError):
        registry.validate_descriptor(config, document)


@pytest.mark.parametrize("change", [
    {"url": "http://user:fixture@example.com/health"}, {"url": "file:///fixture"},
    {"expected_status": 99}, {"expected_status": 600}, {"expected_status": True},
    {"timeout_seconds": 0}, {"timeout_seconds": 31}, {"timeout_seconds": False},
])
def test_http_check_boundaries_match_runtime(declaration, change):
    config, document = declaration
    check = {"type": "http", "url": "http://127.0.0.1:18080/health", "expected_status": 200, "timeout_seconds": 5}
    check.update(change)
    document["checks"] = [check]
    assert list(VALIDATOR.iter_errors(document))
    with pytest.raises(HandbookError):
        registry.validate_descriptor(config, document)


@pytest.mark.parametrize("kind", ["compose", "systemd"])
def test_deployed_manager_requires_its_manager_fields(declaration, kind):
    config, document = declaration
    document.update(kind=kind, state="deployed")
    assert list(VALIDATOR.iter_errors(document))
    with pytest.raises(HandbookError):
        registry.validate_descriptor(config, document)


def test_schema_and_runtime_have_documented_different_responsibilities(declaration):
    config, document = declaration
    outside = Path(document["home"]).parent.parent.parent/"outside"
    document["home"] = str(outside)
    document["readme"] = str(outside/"README.md")
    VALIDATOR.validate(document)
    with pytest.raises(HandbookError):
        registry.validate_descriptor(config, document)
    document = copy.deepcopy(declaration[1])
    document["home"] = str(Path(config["workspace"])/"services/another")
    document["readme"] = str(Path(document["home"])/"README.md")
    document["depends_on"] = [document["id"]]
    VALIDATOR.validate(document)
    with pytest.raises(HandbookError):
        registry.validate_descriptor(config, document)
