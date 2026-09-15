from pathlib import Path

import pytest

from open_agent_artifacts.publication import scan_files


def test_publication_scan_accepts_synthetic_product_files(tmp_path):
    safe = tmp_path / "README.md"
    safe.write_text("Open Agent Artifacts\nhttps://example.com/docs\n", encoding="utf-8")

    assert scan_files([safe]) == []


def test_publication_scan_allows_generic_tailscale_documentation(tmp_path):
    docs = tmp_path / "README.md"
    docs.write_text("Operators may use Tailscale for private access.\n", encoding="utf-8")

    assert scan_files([docs]) == []


def test_publication_scan_allows_loopback_development_addresses(tmp_path):
    docs = tmp_path / "README.md"
    docs.write_text("The local development server listens on 127.0.0.1.\n", encoding="utf-8")

    assert scan_files([docs]) == []


def test_publication_scan_rejects_runtime_database_and_private_paths(tmp_path):
    database = tmp_path / "runtime.db"
    private = tmp_path / "MEMORY.md"
    database.write_text("SQLite format 3", encoding="utf-8")
    private.write_text("private project context", encoding="utf-8")

    issues = scan_files([database, private])

    assert any("forbidden runtime" in issue for issue in issues)
    assert any("private" in issue for issue in issues)


def test_publication_scan_rejects_secret_and_personal_path_patterns(tmp_path):
    candidate = tmp_path / "fixture.txt"
    secret = "ghp_" + "123456789012345678901234567890123456"
    personal_path = "/" + "home/sancho/private"
    candidate.write_text(f"{secret}\n{personal_path}\n", encoding="utf-8")

    issues = scan_files([candidate])

    assert any("secret-like" in issue for issue in issues)
    assert any("personal or operational" in issue for issue in issues)


def test_publication_scan_reports_unreadable_file(tmp_path):
    missing = tmp_path / "does-not-exist.txt"

    issues = scan_files([missing])

    assert issues == [f"{missing}: cannot read file"]
