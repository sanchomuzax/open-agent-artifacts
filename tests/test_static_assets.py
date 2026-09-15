from pathlib import Path

from open_agent_artifacts.server import create_server


def test_installed_style_server_uses_working_directory_web_assets(tmp_path, monkeypatch):
    web_root = tmp_path / "web"
    web_root.mkdir()
    (web_root / "index.html").write_text("<!doctype html><title>cwd asset</title>", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    server = create_server(tmp_path / "installed.db")
    try:
        assert server.static_dir == web_root.resolve()
    finally:
        server.server_close()


def test_service_passes_an_explicit_static_directory():
    service = (Path(__file__).parents[1] / "deploy" / "open-agent-artifacts.service").read_text(encoding="utf-8")

    assert "--static-dir /opt/open-agent-artifacts/web" in service
