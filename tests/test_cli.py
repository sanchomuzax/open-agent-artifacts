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
