# Open Agent Artifacts

A private-by-default artifact workspace for AI agents.

![Open Agent Artifacts workflow](docs/assets/open-agent-artifacts-flow.svg)

The project provides a versioned catalog for agent-produced documents, code, diagrams, and other reviewable artifacts. Users can open a stable link, browse a grid or grouped list, select a passage, leave feedback, inspect version history, restore an earlier version as a new immutable version, and ask an agent to prepare a new version.

The workspace opens on a catalog with visual previews. Markdown opens as a real rendered document; HTML opens as a sandboxed visual document. An opened artifact supports immutable version publishing, pin/unpin, visible or hidden comments, highlighted comment anchors, history, and restore-as-new-version.

## Project description

### English

Open Agent Artifacts is a local-first review workspace for deliverables produced by AI agents. It provides stable artifact links, a searchable catalog, rendered Markdown, isolated HTML presentation, immutable version history, anchored feedback, pinning, and restore-as-new-version. The artifact service owns storage and review state; agents remain responsible for generating content and applying requested changes. The default deployment uses SQLite and keeps runtime data outside the public repository.

### Magyar

Az Open Agent Artifacts egy helyi működésre épülő ellenőrzési munkatér MI-agentek által készített anyagokhoz. Stabil artefaktumlinkeket, kereshető katalógust, renderelt Markdownt, elkülönített HTML-megjelenítést, immutábilis verzióelőzményeket, szövegrészlethez kötött visszajelzést, pinelést és korábbi verzió új változatként történő visszaállítását biztosítja. A tárolást és a review-állapotot az artefaktum-szolgáltatás kezeli; a tartalom elkészítéséért és a kért módosításokért továbbra is az agent felel. Az alapértelmezett telepítés SQLite-ot használ, a futásidejű adatokat pedig a nyilvános repón kívül tartja.

The live workspace—not a repository HTML file—is the primary destination for artifact requests. GitHub contains the source code and documentation.

## Status

Early development. The first release targets static content, immutable versions, anchored comments, a local API, rendered Markdown, and sandboxed HTML presentation. Arbitrary artifact JavaScript execution is intentionally out of scope.

## Design goals

- Agent-independent core with thin client adapters.
- Local-first storage with SQLite.
- Stable artifact links and immutable version history.
- Feedback tied to the exact version and quoted context.
- Safe presentation: Markdown is allowlist-rendered; artifact-provided active content and remote URLs are removed from HTML before it enters an opaque-origin sandbox. A product-owned bridge runs there for resize and selection support.
- Private-by-default deployment; Tailscale can be added by the operator.

## Development

```bash
uv sync --extra dev
uv run pytest -v
```

The repository is designed to run on Python 3.11 or newer. Runtime data must live outside the repository. See `AGENTS.md` for contribution and privacy boundaries.

## Security boundary

This project is not a security guarantee for arbitrary generated code. Markdown is rendered through an allowlist parser. HTML is copied through an element-and-attribute allowlist into an opaque-origin sandbox with a network-denying CSP. Artifact-provided scripts and active or remote content are removed; a product-owned bridge script runs only for frame resizing and text-selection messages. Any future interactive artifact capability must be separate and explicitly reviewed.

## License

MIT. See `LICENSE`.
