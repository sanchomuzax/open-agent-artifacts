from __future__ import annotations

import argparse
import sys
from pathlib import Path

from open_agent_artifacts.description import validate_project_description


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the public bilingual project description")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    issues = validate_project_description(args.root.resolve())
    if issues:
        for issue in issues:
            print(f"BLOCK: {issue}", file=sys.stderr)
        return 1
    print("project description check: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
