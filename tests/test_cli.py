import json
import threading

import pytest

from open_agent_artifacts.cli import main
from open_agent_artifacts.server import create_server


@pytest.fixture
def cli_server(tmp_path):
    server = create_server(tmp_path / "cli.db")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def run_cli(server, capsys, *args):
    exit_code = main(["--base-url", f"http://127.0.0.1:{server.server_port}", *args])
    output = capsys.readouterr()
    assert exit_code == 0, output.err
    return json.loads(output.out)


def test_cli_creates_and_lists_artifact(cli_server, capsys):
    created = run_cli(
        cli_server,
        capsys,
        "create",
        "--title",
        "CLI report",
        "--kind",
        "markdown",
        "--content",
        "# Hello",
        "--created-by",
        "test",
    )
    assert created["title"] == "CLI report"

    listed = run_cli(cli_server, capsys, "list")
    assert [item["id"] for item in listed] == [created["id"]]


def test_cli_publishes_from_a_file(cli_server, capsys, tmp_path):
    created = run_cli(
        cli_server,
        capsys,
        "create",
        "--title",
        "CLI report",
        "--kind",
        "text",
        "--content",
        "old",
        "--created-by",
        "test",
    )
    content_file = tmp_path / "new.txt"
    content_file.write_text("new", encoding="utf-8")

    published = run_cli(
        cli_server,
        capsys,
        "publish",
        created["id"],
        "--file",
        str(content_file),
        "--expected-current-version-id",
        created["current_version_id"],
        "--created-by",
        "test",
    )
    assert published["content"] == "new"
    assert published["sequence"] == 2


def test_cli_lists_gets_and_inboxes_comments(cli_server, capsys):
    created = run_cli(
        cli_server,
        capsys,
        "create",
        "--title",
        "Feedback report",
        "--kind",
        "text",
        "--content",
        "Please review this.",
    )
    version_id = created["current_version_id"]
    comment = run_cli(
        cli_server,
        capsys,
        "feedback",
        version_id,
        "--artifact-id",
        created["id"],
        "--body",
        "Please clarify this.",
        "--exact",
        "review",
    )

    listed = run_cli(cli_server, capsys, "comments", "list", "--status", "open")
    assert [item["id"] for item in listed["items"]] == [comment["id"]]
    assert listed["items"][0]["artifact_title"] == "Feedback report"

    inbox = run_cli(cli_server, capsys, "inbox")
    assert [item["id"] for item in inbox["items"]] == [comment["id"]]

    fetched = run_cli(cli_server, capsys, "comments", "get", comment["id"])
    assert fetched["body"] == "Please clarify this."

    events = run_cli(cli_server, capsys, "comments", "events", comment["id"])
    assert events["items"] == []

    addressed = run_cli(cli_server, capsys, "address", comment["id"], "--status", "addressed", "--version-id", version_id)
    assert addressed["status"] == "addressed"
    assert run_cli(cli_server, capsys, "inbox")["items"] == []


def test_cli_show_returns_current_content_history_and_comments(cli_server, capsys):
    created = run_cli(
        cli_server,
        capsys,
        "create",
        "--title",
        "Show report",
        "--kind",
        "markdown",
        "--content",
        "Current report content",
    )
    comment = run_cli(
        cli_server,
        capsys,
        "feedback",
        created["current_version_id"],
        "--artifact-id",
        created["id"],
        "--body",
        "Please improve this section.",
        "--exact",
        "report",
    )
    shown = run_cli(cli_server, capsys, "show", created["id"])
    assert shown["artifact"]["id"] == created["id"]
    assert shown["current_version"]["content"] == "Current report content"
    assert shown["versions"][0]["id"] == created["current_version_id"]
    assert shown["comments"][0]["id"] == comment["id"]

    summary = run_cli(cli_server, capsys, "show", created["id"], "--summary")
    assert summary["current_version"]["content"] == "Current report content"
    assert summary["current_version"]["truncated"] is False


def test_cli_search_finds_artifact_content_and_feedback(cli_server, capsys):
    created = run_cli(
        cli_server,
        capsys,
        "create",
        "--title",
        "Searchable report",
        "--kind",
        "markdown",
        "--content",
        "A cobalt lighthouse paragraph.",
    )
    comment = run_cli(
        cli_server,
        capsys,
        "feedback",
        created["current_version_id"],
        "--artifact-id",
        created["id"],
        "--body",
        "Please explain the amber harbour.",
        "--exact",
        "lighthouse",
    )
    content_results = run_cli(cli_server, capsys, "search", "cobalt")
    assert any(item["type"] == "artifact" and item["artifact_id"] == created["id"] for item in content_results["items"])
    feedback_results = run_cli(cli_server, capsys, "search", "amber harbour")
    assert any(item["type"] == "comment" and item["comment_id"] == comment["id"] for item in feedback_results["items"])


def test_cli_covers_artifact_lifecycle_operations(cli_server, capsys):
    created = run_cli(
        cli_server,
        capsys,
        "create",
        "--title",
        "Lifecycle report",
        "--kind",
        "text",
        "--content",
        "first",
    )
    versions = run_cli(cli_server, capsys, "versions", "list", created["id"])
    assert [item["id"] for item in versions] == [created["current_version_id"]]

    renamed = run_cli(cli_server, capsys, "rename", created["id"], "--title", "Renamed report")
    assert renamed["title"] == "Renamed report"
    assert run_cli(cli_server, capsys, "pin", created["id"])["pinned"] is True
    assert run_cli(cli_server, capsys, "unpin", created["id"])["pinned"] is False
    assert run_cli(cli_server, capsys, "visit", created["id"])["artifact_id"] == created["id"]

    published = run_cli(
        cli_server,
        capsys,
        "publish",
        created["id"],
        "--content",
        "second",
        "--expected-current-version-id",
        created["current_version_id"],
    )
    restored = run_cli(
        cli_server,
        capsys,
        "restore",
        created["id"],
        "--version-id",
        created["current_version_id"],
        "--expected-current-version-id",
        published["id"],
    )
    assert restored["sequence"] == 3
    assert run_cli(cli_server, capsys, "versions", "list", created["id"])[0]["sequence"] == 3

    duplicate = run_cli(cli_server, capsys, "duplicate", created["id"])
    assert duplicate["id"] != created["id"]
    archived = run_cli(cli_server, capsys, "archive", duplicate["id"])
    assert archived["archived_at"]


def test_cli_diffs_immutable_versions(cli_server, capsys):
    first = run_cli(cli_server, capsys, "create", "--title", "Diff report", "--kind", "text", "--content", "old line\nkeep")
    second = run_cli(
        cli_server,
        capsys,
        "publish",
        first["id"],
        "--content",
        "new line\nkeep",
        "--expected-current-version-id",
        first["current_version_id"],
    )
    result = run_cli(cli_server, capsys, "diff", first["id"], first["current_version_id"], second["id"])
    assert result["format"] == "unified"
    assert "-old line" in result["diff"]
    assert "+new line" in result["diff"]

    other = run_cli(cli_server, capsys, "create", "--title", "Other", "--kind", "text", "--content", "other")
    exit_code = main([
        "--base-url", f"http://127.0.0.1:{cli_server.server_port}",
        "diff", first["id"], first["current_version_id"], other["current_version_id"],
    ])
    error = capsys.readouterr()
    assert exit_code == 1
    assert "different artifact" in error.err

    main([
        "--base-url", f"http://127.0.0.1:{cli_server.server_port}",
        "diff", first["id"], first["current_version_id"], second["id"], "--format", "unified",
    ])
    plain = capsys.readouterr()
    assert plain.out.startswith("--- ")
    assert not plain.out.startswith('"')


def test_cli_checks_comment_anchor_status_and_unicode_offsets(cli_server, capsys):
    created = run_cli(cli_server, capsys, "create", "--title", "Anchors", "--kind", "text", "--content", "🚀 alpha beta")
    anchor = {
        "schema": 2,
        "type": "text-range",
        "coordinate_space": "unicode-code-points",
        "start": 2,
        "end": 7,
        "quote": {"exact": "alpha", "prefix": "🚀 ", "suffix": " beta"},
    }
    comment = run_cli(
        cli_server, capsys, "feedback", created["current_version_id"],
        "--artifact-id", created["id"], "--body", "Check alpha.",
        "--anchor-json", json.dumps(anchor),
    )
    checked = run_cli(cli_server, capsys, "anchor", "check", comment["id"], "--version", created["current_version_id"])
    assert checked["status"] == "exact"
    assert checked["match_count"] == 1
    assert checked["matches"][0]["start"] == 2

    duplicate = run_cli(cli_server, capsys, "create", "--title", "Duplicate anchors", "--kind", "text", "--content", "alpha one\nalpha two")
    duplicate_comment = run_cli(
        cli_server, capsys, "feedback", duplicate["current_version_id"],
        "--artifact-id", duplicate["id"], "--body", "Which alpha?", "--exact", "alpha",
    )
    duplicate_check = run_cli(cli_server, capsys, "anchor", "check", duplicate_comment["id"], "--version", duplicate["current_version_id"])
    assert duplicate_check["status"] == "ambiguous"
    assert duplicate_check["match_count"] == 2

    current = run_cli(
        cli_server, capsys, "publish", created["id"], "--content", "🚀 gamma beta",
        "--expected-current-version-id", created["current_version_id"],
    )
    missing = run_cli(cli_server, capsys, "anchor", "check", comment["id"], "--version", current["id"])
    assert missing["status"] == "missing"
