from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from open_agent_artifacts.agent_skill import AgentSkillInstallError, install_agent_skill
from open_agent_artifacts.cli import main
from open_agent_artifacts.server import create_server
from open_agent_artifacts.store import Store, ValidationError


ROOT = Path(__file__).parents[1]


def request(server, method, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request_obj = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request_obj, timeout=3) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_hermes_skill_install_detects_profile_is_idempotent_and_writes_no_token(tmp_path, monkeypatch):
    hermes_home = tmp_path / "profiles" / "reviewer"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("OAA_API_TOKEN", "secret-token-must-not-be-copied")

    first = install_agent_skill("hermes")
    target = hermes_home / "skills" / "open-agent-artifacts" / "SKILL.md"

    assert Path(first["target_path"]) == target
    assert first["changed"] is True
    assert first["skill_version"]
    assert len(first["sha256"]) == 64
    assert target.is_file()
    assert "secret-token-must-not-be-copied" not in target.read_text(encoding="utf-8")

    second = install_agent_skill("hermes")
    assert second["changed"] is False
    assert second["sha256"] == first["sha256"]
    assert target.read_text(encoding="utf-8") == target.read_text(encoding="utf-8")


def test_skill_install_rejects_unknown_agent_and_invalid_target(tmp_path):
    with pytest.raises(AgentSkillInstallError, match="unsupported agent"):
        install_agent_skill("unknown", home=tmp_path / "unknown")

    invalid_home = tmp_path / "hermes-home"
    (invalid_home / "skills").parent.mkdir(parents=True)
    (invalid_home / "skills").write_text("not a directory", encoding="utf-8")
    with pytest.raises(AgentSkillInstallError, match="directory"):
        install_agent_skill("hermes", home=invalid_home)
    assert not (invalid_home / "skills" / "open-agent-artifacts").exists()


def test_skill_install_rejects_symlinked_skill_root(tmp_path):
    hermes_home = tmp_path / "hermes-home"
    outside = tmp_path / "outside"
    hermes_home.mkdir()
    outside.mkdir()
    (hermes_home / "skills").symlink_to(outside, target_is_directory=True)

    with pytest.raises(AgentSkillInstallError, match="symlink"):
        install_agent_skill("hermes", home=hermes_home)
    assert not (outside / "open-agent-artifacts").exists()


def test_skill_install_does_not_overwrite_modified_skill_without_force(tmp_path):
    target = tmp_path / "skills" / "open-agent-artifacts" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("---\nname: user-owned\n---\n", encoding="utf-8")

    with pytest.raises(AgentSkillInstallError, match="different content"):
        install_agent_skill("hermes", home=tmp_path)

    result = install_agent_skill("hermes", home=tmp_path, force=True)
    assert result["changed"] is True
    assert target.read_text(encoding="utf-8").startswith("---\nname: open-agent-artifacts")


def test_metadata_is_validated_returned_filtered_and_audited(tmp_path):
    store = Store(tmp_path / "metadata.db")
    metadata = {
        "schema_version": 1,
        "tags": ["review", "release"],
        "source_agent": "hermes",
        "project": "artifacts",
        "purpose": "release review",
        "content_language": "en",
    }
    artifact = store.create_artifact("Release", "markdown", "release body", "test", metadata=metadata)

    assert artifact["metadata"] == metadata
    assert store.get_artifact(artifact["id"])["metadata"] == metadata
    assert store.list_artifacts(project="artifacts", tags=["review"], source_agent="hermes")[0]["id"] == artifact["id"]
    assert store.list_artifacts(project="other") == []

    published = store.publish_version(
        artifact["id"],
        "release body v2",
        "test",
        expected_current_version_id=artifact["current_version_id"],
        metadata={
            "tags": ["release", "v2"],
            "source_agent": "codex",
            "project": "artifacts",
            "purpose": "published release",
            "content_language": "en",
        },
    )
    assert published["metadata"]["tags"] == ["release", "v2"]
    assert store.get_current_version(artifact["id"])["content"] == "release body v2"
    assert store.get_artifact(artifact["id"])["metadata"]["source_agent"] == "codex"
    assert len(store.list_metadata_events(artifact["id"])) == 2


def test_metadata_defaults_and_invalid_values_are_stable(tmp_path):
    store = Store(tmp_path / "metadata-validation.db")
    artifact = store.create_artifact("Default", "text", "body", "test")
    assert artifact["metadata"] == {
        "schema_version": 1,
        "tags": [],
        "source_agent": None,
        "project": None,
        "purpose": None,
        "content_language": None,
    }

    with pytest.raises(ValidationError, match="unknown metadata field"):
        store.create_artifact("Unknown", "text", "body", "test", metadata={"secret": "no"})
    with pytest.raises(ValidationError, match="metadata purpose exceeds"):
        store.create_artifact("Too large", "text", "body", "test", metadata={"purpose": "x" * 20_000})
    with pytest.raises(ValidationError, match="tags"):
        store.create_artifact("Bad tags", "text", "body", "test", metadata={"tags": ["ok", 3]})


def test_metadata_filters_are_available_through_api_and_search(tmp_path):
    server = create_server(tmp_path / "api.db", instance_id="test-issue-15-17", storage_class="persistent")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, artifact = request(server, "POST", "/api/artifacts", {
            "title": "Agent report",
            "kind": "markdown",
            "content": "unique discovery text",
            "created_by": "test",
            "metadata": {"tags": ["ops"], "project": "alpha", "source_agent": "hermes"},
        })
        assert status == 201
        assert artifact["metadata"]["project"] == "alpha"

        status, listed = request(server, "GET", "/api/artifacts?project=alpha&tag=ops&source_agent=hermes")
        assert status == 200
        assert [item["id"] for item in listed] == [artifact["id"]]

        status, search = request(server, "GET", "/api/search?q=discovery&project=alpha&tag=ops")
        assert status == 200
        assert search["items"][0]["metadata"]["project"] == "alpha"

        status, published = request(server, "POST", f"/api/artifacts/{artifact['id']}/versions", {
            "content": "unique discovery text v2",
            "created_by": "test",
            "expected_current_version_id": artifact["current_version_id"],
            "metadata": {"tags": ["ops", "v2"], "project": "alpha", "source_agent": "hermes"},
        })
        assert status == 201
        assert published["metadata"]["tags"] == ["ops", "v2"]

        status, history = request(server, "GET", f"/api/artifacts/{artifact['id']}/metadata/events")
        assert status == 200
        assert len(history) == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_portable_hermes_plugin_package_has_v1_layout_and_safe_manifest():
    manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    skill = ROOT / "skills" / "open-agent-artifacts" / "SKILL.md"

    assert manifest["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert manifest["name"] == "open-agent-artifacts"
    assert manifest["version"]
    assert manifest["repository"] == "https://github.com/sanchomuzax/open-agent-artifacts"
    assert manifest["license"] == "MIT"
    assert skill.is_file()
    assert "OAA_API_TOKEN=" not in skill.read_text(encoding="utf-8")
    assert not (ROOT / "mcp.json").exists()


def test_cli_entry_points_cover_metadata_filters_and_skill_install(tmp_path, capsys):
    server = create_server(tmp_path / "cli.db", instance_id="test-issue-15-17-cli", storage_class="persistent")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert main([
            "--base-url", base,
            "create", "--title", "CLI metadata", "--kind", "text", "--content", "discoverable",
            "--metadata-json", '{"tags":["cli"],"project":"entry","source_agent":"hermes"}',
        ]) == 0
        created = json.loads(capsys.readouterr().out)
        assert created["metadata"]["project"] == "entry"

        assert main(["--base-url", base, "list", "--project", "entry", "--tag", "cli"]) == 0
        listed = json.loads(capsys.readouterr().out)
        assert listed[0]["id"] == created["id"]

        assert main(["--base-url", base, "search", "discoverable", "--project", "entry"]) == 0
        searched = json.loads(capsys.readouterr().out)
        assert searched["items"][0]["metadata"]["source_agent"] == "hermes"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

    install_home = tmp_path / "cli-hermes"
    assert main(["install-agent-skill", "--agent", "hermes", "--hermes-home", str(install_home)]) == 0
    installed = json.loads(capsys.readouterr().out)
    assert Path(installed["target_path"]).is_file()
