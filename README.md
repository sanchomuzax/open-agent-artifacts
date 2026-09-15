# Open Agent Artifacts

A private-by-default artifact workspace for AI agents.

The project provides a versioned catalog for agent-produced documents, code, diagrams, and other reviewable artifacts. Users can open a stable link, select a passage, leave feedback, compare versions, and ask an agent to prepare a new version.

## Status

Early development. The first release targets static content, immutable versions, anchored comments, and a local API. Arbitrary artifact code execution is intentionally out of scope.

## Design goals

- Agent-independent core with thin client adapters.
- Local-first storage with SQLite.
- Stable artifact links and immutable version history.
- Feedback tied to the exact version and quoted context.
- Safe static preview: generated content is displayed as text, not executed.
- Private-by-default deployment; Tailscale can be added by the operator.

## Development

```bash
uv sync --extra dev
uv run pytest -v
```

The repository is designed to run on Python 3.11 or newer. Runtime data must live outside the repository. See `AGENTS.md` for contribution and privacy boundaries.

## Security boundary

This project is not a security guarantee for arbitrary generated code. The static preview deliberately does not execute artifact JavaScript or HTML. Any future interactive preview must be a separate, explicitly reviewed capability with a stronger isolation boundary.

## License

MIT. See `LICENSE`.
