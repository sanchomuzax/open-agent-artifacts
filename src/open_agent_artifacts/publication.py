from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

FORBIDDEN_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".log", ".pem", ".key")
FORBIDDEN_NAMES = {".env", ".env.local", ".env.production", "memory.md", "todo.md"}
FORBIDDEN_COMPONENTS = {"runtime", "backups", "secrets", "sessions", "private"}
SECRET_PATTERNS = (
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
PERSONAL_PATTERNS = (
    re.compile(r"/home/[A-Za-z0-9_.-]+(?:/|$)"),
    re.compile(r"(?:notebooklm\.google\.com|docs\.google\.com|telegram\.me)", re.IGNORECASE),
    re.compile(r"\b(?:10\.(?:[0-9]{1,3}\.){2}[0-9]{1,3}|172\.(?:1[6-9]|2[0-9]|3[0-1])\.(?:[0-9]{1,3}\.)[0-9]{1,3}|192\.168\.(?:[0-9]{1,3}\.)[0-9]{1,3})\b"),
    re.compile(r"\b[A-Za-z0-9-]+\.ts\.net\b", re.IGNORECASE),
)


def _path_issues(path: Path) -> list[str]:
    lower_name = path.name.lower()
    components = {part.lower() for part in path.parts}
    issues: list[str] = []
    if lower_name in FORBIDDEN_NAMES or any(part in components for part in FORBIDDEN_COMPONENTS):
        issues.append(f"{path}: private project or runtime file")
    if lower_name.endswith(FORBIDDEN_SUFFIXES) or any(lower_name.startswith(f"{suffix}-") for suffix in FORBIDDEN_SUFFIXES):
        issues.append(f"{path}: forbidden runtime or secret file")
    return issues


def scan_files(paths: list[Path]) -> list[str]:
    issues: list[str] = []
    for path in paths:
        issues.extend(_path_issues(path))
        if path.resolve() == Path(__file__).resolve():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            issues.append(f"{path}: cannot read file")
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(content):
                issues.append(f"{path}: secret-like content")
                break
        for pattern in PERSONAL_PATTERNS:
            if pattern.search(content):
                issues.append(f"{path}: personal or operational content")
                break
    return sorted(set(issues))


def tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    names = [name for name in result.stdout.decode("utf-8").split("\0") if name]
    return [root / name for name in names]


def staged_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(root), "diff", "--cached", "--name-only", "-z"],
        check=True,
        capture_output=True,
    )
    names = [name for name in result.stdout.decode("utf-8").split("\0") if name]
    return [root / name for name in names]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check files before publishing the public repository")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--staged", action="store_true", help="scan staged files instead of all tracked files")
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.staged and args.paths:
        parser.error("--staged cannot be combined with explicit paths")
    paths = [path if path.is_absolute() else root / path for path in args.paths] if args.paths else (
        staged_files(root) if args.staged else tracked_files(root)
    )
    issues = scan_files(paths)
    if issues:
        for issue in issues:
            print(f"BLOCK: {issue}", file=sys.stderr)
        return 1
    print(f"publication scan: PASS ({len(paths)} files)")
    return 0
