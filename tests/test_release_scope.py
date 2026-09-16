from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

_MODULE_PATH = Path(__file__).parents[1] / "scripts" / "release_scope.py"
_SPEC = importlib.util.spec_from_file_location("release_scope", _MODULE_PATH)
assert _SPEC and _SPEC.loader
release_scope = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(release_scope)
ZERO_SHA = release_scope.ZERO_SHA
decide = release_scope.decide


def run(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def commit(repo: Path, path: str, content: str, message: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    run(repo, "add", path)
    run(repo, "commit", "-m", message)
    return run(repo, "rev-parse", "HEAD")


def make_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    run(repo, "init")
    run(repo, "config", "user.email", "test@example.invalid")
    run(repo, "config", "user.name", "Test")
    sha = commit(repo, "pyproject.toml", '[project]\nversion = "1.2.3"\n', "initial")
    return repo, sha


def test_product_change_before_docs_only_head_still_releases(tmp_path):
    repo, base = make_repo(tmp_path)
    commit(repo, "src/app.py", "value = 1\n", "product")
    head = commit(repo, "README.md", "docs\n", "docs")
    result = decide(repo, "push", base, head)
    assert result["release_needed"] == "true"
    assert result["reason"] == "product-change"


def test_docs_only_push_skips_release(tmp_path):
    repo, base = make_repo(tmp_path)
    head = commit(repo, "README.md", "docs\n", "docs")
    assert decide(repo, "push", base, head)["reason"] == "docs-only"


def test_existing_version_tag_skips_without_failure(tmp_path):
    repo, base = make_repo(tmp_path)
    head = commit(repo, "web/app.js", "const x = 1;\n", "product")
    run(repo, "tag", "v1.2.3")
    result = decide(repo, "push", base, head)
    assert result["release_needed"] == "false"
    assert result["reason"] == "tag-exists"


def test_zero_before_sha_handles_first_push(tmp_path):
    repo, head = make_repo(tmp_path)
    result = decide(repo, "push", ZERO_SHA, head)
    assert result["product_changed"] == "true"


def test_zero_before_sha_scans_product_change_before_docs_tip(tmp_path):
    repo, _ = make_repo(tmp_path)
    commit(repo, "web/app.js", "const value = 1;\n", "product")
    head = commit(repo, "README.md", "docs only tip\n", "docs")
    result = decide(repo, "push", ZERO_SHA, head)
    assert result["product_changed"] == "true"
    assert result["release_needed"] == "true"
