from __future__ import annotations

import argparse

from open_agent_artifacts.backup import backup_database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a validated Open Agent Artifacts database backup")
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    args = parser.parse_args(argv)
    destination = backup_database(args.source, args.destination)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
