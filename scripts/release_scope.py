from __future__ import annotations

import argparse
import subprocess
import tomllib
from pathlib import Path

PRODUCT_PREFIXES = ("src/", "web/", "scripts/")
PRODUCT_FILES = {"pyproject.toml", "uv.lock"}
ZERO_SHA = "0" * 40


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], text=True, capture_output=True, check=False
    )
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def changed_paths(repo: Path, event: str, before: str, sha: str) -> list[str]:
    if event == "workflow_dispatch":
        return ["<manual>"]
    if before and before != ZERO_SHA:
        output = git(repo, "diff", "--name-only", f"{before}..{sha}")
    else:
        output = git(repo, "log", "--format=", "--name-only", sha)
    return [line for line in output.splitlines() if line]


def decide(repo: Path, event: str, before: str, sha: str) -> dict[str, str]:
    version = tomllib.loads((repo / "pyproject.toml").read_text())["project"]["version"]
    tag = f"v{version}"
    paths = changed_paths(repo, event, before, sha)
    product_changed = event == "workflow_dispatch" or any(
        path in PRODUCT_FILES or path.startswith(PRODUCT_PREFIXES) for path in paths
    )
    tag_exists = bool(git(repo, "show-ref", "--verify", f"refs/tags/{tag}", check=False))
    release_needed = product_changed and not tag_exists
    reason = "manual" if event == "workflow_dispatch" else "product-change"
    if not product_changed:
        reason = "docs-only"
    elif tag_exists:
        reason = "tag-exists"
    return {
        "product_changed": str(product_changed).lower(),
        "version": version,
        "release_needed": str(release_needed).lower(),
        "reason": reason,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--before", default="")
    parser.add_argument("--sha", required=True)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    result = decide(args.repo.resolve(), args.event, args.before, args.sha)
    text = "\n".join(f"{key}={value}" for key, value in result.items()) + "\n"
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as handle:
            handle.write(text)
    print(text, end="")


if __name__ == "__main__":
    main()
