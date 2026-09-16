# Open Agent Artifacts

A private-by-default artifact workspace for AI agents.

![Open Agent Artifacts workflow](docs/assets/open-agent-artifacts-flow.svg)

The project provides a versioned catalog for agent-produced documents, code, diagrams, and other reviewable artifacts. Users can open a stable link, browse a grid or grouped list, select a passage, leave feedback, inspect version history, restore an earlier version as a new immutable version, and ask an agent to prepare a new version.

The workspace opens on a catalog with visual previews. Markdown opens as a real rendered document; HTML opens as a sandboxed visual document. An opened artifact supports immutable version publishing, pin/unpin, visible or hidden comments, highlighted comment anchors, history, and restore-as-new-version.

## Project description

Open Agent Artifacts is a local-first review workspace for deliverables produced by AI agents. It provides stable artifact links, a searchable catalog, rendered Markdown, isolated HTML presentation, immutable version history, anchored feedback, pinning, and restore-as-new-version. The artifact service owns storage and review state; agents remain responsible for generating content and applying requested changes. The default deployment uses SQLite and keeps runtime data outside the public repository.

The live workspace—not a repository HTML file—is the primary destination for artifact requests. GitHub contains the source code and documentation.

## Using it with an AI agent

Open Agent Artifacts is a review workspace, not a model and not an autonomous coding agent. An agent publishes a document or other deliverable here; a person opens the stable artifact link, reviews the immutable version, and leaves feedback on a selected passage. The agent then reads that feedback, prepares a new version, and publishes it without overwriting the old one.

The shortest agent workflow is:

1. Make sure the Artifacts service is running and set `OAA_URL` to its private URL.
2. Create an artifact with `artifactctl create`.
3. Return the verified artifact ID/link to the user.
4. When asked to address feedback, fetch the current artifact and its open comments.
5. Publish a new version with `artifactctl publish`, passing the current version ID.
6. Read the new version back before saying the feedback was addressed.

The repository includes the complete command examples in [`docs/agent-integration.md`](docs/agent-integration.md). After installing `artifactctl`, install the bundled Hermes skill with `artifactctl install-agent-skill --agent hermes`; it follows `HERMES_HOME` (including named profiles), reports its target and SHA-256, and never overwrites different user-owned content without `--force`. `AGENTS.md` is only the public contributor guide and is not the artifact runtime configuration.

Artifacts carry bounded structured metadata (`tags`, `source_agent`, `project`, `purpose`, and `content_language`) with schema version 1. Agents can filter the catalog and search by these fields. Metadata is returned consistently by the API and CLI, changes are audited, and immutable content versions remain unchanged.

The repository is also a portable Hermes Agent Plugins v1 package: [`plugin.json`](plugin.json) declares the plugin and [`skills/open-agent-artifacts/SKILL.md`](skills/open-agent-artifacts/SKILL.md) supplies the workflow skill. It contains no MCP server or credentials; enablement and catalog admission remain explicit user/maintainer actions.

Comments are stored feedback, not automatic commands. The service does not push comments to an agent by itself. An agent sees them when it is explicitly asked to process feedback or when a separately configured polling/webhook integration fetches them. A comment alone must never authorize unrelated or destructive actions.

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
