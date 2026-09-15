from __future__ import annotations

import argparse

from open_agent_artifacts.backup import restore_database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Restore a validated Open Agent Artifacts database backup")
    parser.add_argument("--backup", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    destination = restore_database(args.backup, args.destination, overwrite=args.overwrite)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
