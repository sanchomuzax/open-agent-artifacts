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
