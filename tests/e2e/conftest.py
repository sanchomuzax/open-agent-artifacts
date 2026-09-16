from __future__ import annotations

import json
import os
import threading
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, Playwright, sync_playwright

from open_agent_artifacts.server import create_server


ROOT = Path(__file__).parents[2]


@pytest.fixture(scope="session")
def playwright_instance():
    with sync_playwright() as playwright:
        yield playwright


@pytest.fixture(scope="session")
def browser(playwright_instance: Playwright):
    browser_path = os.environ.get("OAA_E2E_CHROMIUM")
    if os.environ.get("OAA_E2E_BROWSER") == "system" and not browser_path:
        browser_path = "/usr/bin/chromium"
    try:
        instance = playwright_instance.chromium.launch(
            headless=True,
            executable_path=browser_path or None,
        )
    except Exception as error:  # pragma: no cover - environment-specific setup
        pytest.skip(f"Playwright browser unavailable: {error}")
    yield instance
    instance.close()


@pytest.fixture
def running_server(tmp_path: Path):
    server = create_server(tmp_path / "artifacts.db", static_dir=ROOT / "web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.fixture
def page(browser: Browser, running_server) -> Page:
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    context.add_init_script("if (window.top === window) { localStorage.removeItem('oaa_view_mode'); sessionStorage.clear(); }")
    current = context.new_page()
    current.base_url = f"http://127.0.0.1:{running_server.server_port}"
    yield current
    context.close()


def create_artifact(server, title: str, kind: str, content: str) -> dict:
    payload = json.dumps({"title": title, "kind": kind, "content": content, "created_by": "e2e"}).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}/api/artifacts",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        return json.loads(response.read())
