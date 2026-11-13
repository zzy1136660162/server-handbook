"""Concurrent edits and existing symlinks must not redirect or overwrite user files."""
from unittest.mock import patch

import pytest

from server_handbook import config, documents, registry
from server_handbook.errors import HandbookError


@pytest.mark.parametrize("filename", ["README.md", "AGENTS.md"])
@pytest.mark.parametrize("initially_exists", [True, False])
def test_adopt_retains_manual_file_changed_after_its_snapshot(tmp_path, filename, initially_exists):
    workspace = tmp_path / "workspace"
    cfg = config.initialize(workspace, apply=True)["config"]
    home = workspace / "projects" / "legacy"
    home.mkdir()
    target = home / filename
    original = f"# Owner's {filename}\r\n"
    edited = f"# Owner's concurrent {filename} change\r\n".encode("utf-8")
    if initially_exists:
        target.write_bytes(original.encode("utf-8"))
    body_heading = "## Server Handbook deployment" if filename == "README.md" else "## Server Handbook workflow"
    real_managed_text = documents.managed_text

    def edit_while_rendering(previous, body):
        rendered = real_managed_text(previous, body)
        if body.startswith(body_heading):
            target.write_bytes(edited)
        return rendered

    with patch.object(documents, "managed_text", side_effect=edit_while_rendering):
        with pytest.raises(HandbookError, match="changed|already exists"):
            documents.adopt(cfg, home, "legacy", "Legacy", apply=True)
    assert target.read_bytes() == edited
    assert registry.list_services(cfg)["total"] == 0


@pytest.mark.parametrize("filename", ["README.md", "AGENTS.md"])
def test_init_retains_manual_change_between_rendering_and_write(tmp_path, filename):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / filename
    target.write_bytes(b"# Existing owner instructions\r\n")
    edited = b"# Concurrent owner instructions that must survive\r\n"
    real_initialize_database = registry.initialize_database

    def edit_after_rendering(cfg):
        real_initialize_database(cfg)
        target.write_bytes(edited)

    with patch.object(registry, "initialize_database", side_effect=edit_after_rendering):
        with pytest.raises(HandbookError, match="changed"):
            config.initialize(workspace, apply=True)
    assert target.read_bytes() == edited


def test_adopt_never_replaces_concurrently_created_descriptor(tmp_path):
    workspace = tmp_path / "workspace"
    cfg = config.initialize(workspace, apply=True)["config"]
    home = workspace / "projects" / "legacy"
    home.mkdir()
    readme = home / "README.md"
    readme.write_bytes(b"# Owner instructions\r\n")
    source = home / "deploy" / "registry" / "legacy.json"
    competing = b'{"owner":"another agent prepared this declaration"}\n'
    real_descriptor_json = documents._descriptor_json

    def concurrent_preparation(descriptor):
        source.parent.mkdir(parents=True)
        source.write_bytes(competing)
        return real_descriptor_json(descriptor)

    with patch.object(documents, "_descriptor_json", side_effect=concurrent_preparation):
        with pytest.raises(HandbookError, match="already exists"):
            documents.adopt(cfg, home, "legacy", "Legacy", apply=True)
    assert source.read_bytes() == competing
    assert readme.read_bytes() == b"# Owner instructions\r\n"


@pytest.mark.parametrize("inside_workspace", [True, False])
def test_adopt_rejects_deploy_symlink_outside_service_home(tmp_path, inside_workspace):
    workspace = tmp_path / "workspace"
    cfg = config.initialize(workspace, apply=True)["config"]
    home = workspace / "projects" / "legacy"
    home.mkdir()
    readme = home / "README.md"
    readme.write_bytes(b"# Existing project\r\n")
    destination = (workspace if inside_workspace else tmp_path) / "unrelated"
    destination.mkdir()
    try:
        (home / "deploy").symlink_to(destination, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symlinks is not permitted on this host")

    with pytest.raises(HandbookError, match="outside configured|inside the service home"):
        documents.adopt(cfg, home, "legacy", "Legacy", apply=True)
    assert list(destination.iterdir()) == []
    assert readme.read_bytes() == b"# Existing project\r\n"
    assert not (home / "AGENTS.md").exists()


@pytest.mark.parametrize("link_directory", [True, False])
def test_init_rejects_config_symlink_before_creating_state(tmp_path, link_directory):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    readme = workspace / "README.md"
    readme.write_bytes(b"# Workspace owner\r\n")
    outside = tmp_path / "unrelated"
    outside.mkdir()
    settings = workspace / ".server-handbook"
    try:
        if link_directory:
            settings.symlink_to(outside, target_is_directory=True)
        else:
            settings.mkdir()
            (settings / "config.json").symlink_to(outside / "config.json")
    except OSError:
        pytest.skip("Creating symlinks is not permitted on this host")

    with pytest.raises(HandbookError, match="symbolic links|symlink"):
        config.initialize(workspace, apply=True)
    assert list(outside.iterdir()) == []
    assert not (settings / "state.sqlite3").exists()
    assert readme.read_bytes() == b"# Workspace owner\r\n"
