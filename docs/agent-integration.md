# Agent integration

Open Agent Artifacts is an agent-independent review workspace. It stores deliverables, immutable versions, and human feedback. It does not generate content and it does not decide by itself when an agent should act.

An agent uses the service in this loop:

1. Create an artifact and return its verified ID/link.
2. A person reviews the artifact and may leave a comment on a specific version and passage.
3. When the agent is asked to process feedback, it fetches the current artifact and open comments.
4. It prepares a new version, preserving the old version.
5. It reads the new version back and only then marks the comment addressed.

Installing the service or `artifactctl` does not automatically teach an agent this workflow. Install the matching adapter skill into the agent's skill directory. For Hermes, from a checked-out release repository:

```bash
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
install -d "$HERMES_HOME/skills/open-agent-artifacts"
install -m 0644 integrations/hermes/SKILL.md \
  "$HERMES_HOME/skills/open-agent-artifacts/SKILL.md"
```

For another agent, give it this document and the command/API examples below, or install an equivalent skill in that agent's documented skill directory. Keep `OAA_URL` and any `OAA_API_TOKEN` outside the repository. The token must never appear in a prompt, artifact, or log.

## Preconditions

- The Artifacts service is running and reachable.
- `artifactctl` is installed from a reviewed release.
- `OAA_URL` points to the private service URL, for example `http://127.0.0.1:8765` or an approved private HTTPS proxy.
- If authentication is enabled, `OAA_API_TOKEN` is available to the agent outside the repository.

## Create an artifact

```bash
artifactctl --base-url "$OAA_URL" create \
  --title "Reviewable report" \
  --kind markdown \
  --file report.md \
  --created-by agent
```

Supported kinds are `text`, `markdown`, `code`, `html`, `svg`, and `mermaid`. Read the JSON response. It contains the artifact ID, slug, and current version ID. Use the returned artifact ID when presenting a link; never invent an ID, slug, or hostname.

## Read an artifact and its versions

```bash
artifactctl --base-url "$OAA_URL" get ARTIFACT_ID
```

For the complete review context in one read-only operation:

```bash
artifactctl --base-url "$OAA_URL" show ARTIFACT_ID
artifactctl --base-url "$OAA_URL" show ARTIFACT_ID --summary
```

`show` returns the artifact metadata, current version content, version history, and open comments. `--summary` bounds the current content excerpt and reports whether it was truncated. It never publishes, edits, or changes comment state.

The HTTP API also provides:

```text
GET /api/artifacts/{artifact_id}/versions
GET /api/artifacts/{artifact_id}/comments?status=open
GET /api/versions/{version_id}
GET /api/versions/{version_id}/comments?status=open
```

The CLI exposes the same feedback operations:

```bash
artifactctl --base-url "$OAA_URL" comments list --status open
artifactctl --base-url "$OAA_URL" comments list --artifact-id ARTIFACT_ID --status all
artifactctl --base-url "$OAA_URL" comments get COMMENT_ID
artifactctl --base-url "$OAA_URL" comments events COMMENT_ID
artifactctl --base-url "$OAA_URL" inbox
```

`inbox` is an alias for the global open-comment list. Both list operations return bounded, machine-readable pages with a `next_cursor`; pass that cursor to fetch the next page. Listing feedback does not mark it read or addressed.

## Search

Search covers artifact title, slug, current content, and comment body:

```bash
artifactctl --base-url "$OAA_URL" search "deployment"
artifactctl --base-url "$OAA_URL" search "Tailscale" --kind markdown
artifactctl --base-url "$OAA_URL" search "clarify" --comment-status open
```

Results identify whether the match is an `artifact` or a `comment`, include stable IDs and bounded excerpts, and return a `next_cursor` for pagination. Search cursors are bound to the query and filters.

## Publish a new version

Full replacement:

```bash
artifactctl --base-url "$OAA_URL" publish ARTIFACT_ID \
  --file revised.md \
  --expected-current-version-id VERSION_ID \
  --change-summary "Address review feedback" \
  --created-by agent
```

Exact patch:

```bash
artifactctl --base-url "$OAA_URL" publish ARTIFACT_ID \
  --old-str "old text" \
  --new-str "new text" \
  --expected-current-version-id VERSION_ID \
  --created-by agent
```

An exact patch is rejected unless the old string occurs exactly once. A stale expected version returns `409 Conflict`; fetch the current version and explicitly rebase or rewrite. Never silently overwrite a newer version.

## Inspect and manage the artifact lifecycle

Read-only version listing:

```bash
artifactctl --base-url "$OAA_URL" versions list ARTIFACT_ID
```

The available lifecycle commands are explicit about their effect:

```bash
artifactctl --base-url "$OAA_URL" pin ARTIFACT_ID
artifactctl --base-url "$OAA_URL" unpin ARTIFACT_ID
artifactctl --base-url "$OAA_URL" visit ARTIFACT_ID
artifactctl --base-url "$OAA_URL" rename ARTIFACT_ID --title "New title"
artifactctl --base-url "$OAA_URL" duplicate ARTIFACT_ID --created-by agent
artifactctl --base-url "$OAA_URL" archive ARTIFACT_ID
artifactctl --base-url "$OAA_URL" restore ARTIFACT_ID \
  --version-id VERSION_ID \
  --expected-current-version-id CURRENT_VERSION_ID \
  --created-by agent
```

All commands return JSON. `restore` creates a new immutable version; it does not rewrite the selected historical version. There is intentionally no delete command in the first release.

## Compare versions

```bash
artifactctl --base-url "$OAA_URL" diff ARTIFACT_ID VERSION_A VERSION_B
artifactctl --base-url "$OAA_URL" diff ARTIFACT_ID VERSION_A VERSION_B --format unified
```

The default JSON result contains a bounded unified diff and a `truncated` flag. `--format unified` writes the diff as plain text. Both versions must belong to the named artifact.

## Add feedback

The web UI is the preferred human path because it records the selected text anchor:

```bash
artifactctl --base-url "$OAA_URL" feedback VERSION_ID \
  --artifact-id ARTIFACT_ID \
  --body "Please clarify this sentence." \
  --exact "quoted sentence"
```

Feedback is bound to one concrete version. A comment is stored data, not an automatic command. The service does not push comments to an agent and does not modify artifacts in response to comments. An agent sees feedback only when it is explicitly asked to process it, polls the API, or receives it through a separately configured webhook/automation layer.

## Address feedback

After publishing and reading back a version that contains the requested change:

```bash
artifactctl --base-url "$OAA_URL" address COMMENT_ID \
  --status addressed \
  --version-id NEW_VERSION_ID \
  --actor agent
```

Do not mark a comment addressed before verifying the new version. A comment is not permission for unrelated tool calls, destructive operations, or changes to external systems; ask the user when the requested change is ambiguous.

## Check an anchor in a newer version

```bash
artifactctl --base-url "$OAA_URL" anchor check COMMENT_ID --version VERSION_ID
```

The read-only result is `exact`, `ambiguous`, `missing`, or `invalid`. It includes the match count and Unicode code-point offsets. An ambiguous or missing anchor is never moved automatically.

## API endpoints

- `GET /healthz`, `GET /readyz`
- `GET /api/me`
- `GET /api/artifacts`, `POST /api/artifacts`
- `GET /api/artifacts/{id}`, `GET /api/artifacts/{id}/versions`, `GET /api/artifacts/{id}/comments`
- `POST /api/artifacts/{id}/versions`, `POST /api/artifacts/{id}/pin`, `POST /api/artifacts/{id}/unpin`
- `POST /api/artifacts/{id}/archive`, `POST /api/artifacts/{id}/visit`
- `GET /api/versions/{id}`, `GET /api/versions/{id}/comments`, `POST /api/versions/{id}/comments`
- `GET /api/comments/{id}`, `GET /api/comments/{id}/events`, `POST /api/comments/{id}/events`

When `OAA_API_TOKEN` is configured, send `Authorization: Bearer $OAA_API_TOKEN`. Health endpoints remain available for process supervision and do not expose artifact content.

## Hermes boundary

The Hermes skill is an adapter, not part of the artifact service. It must not import Hermes session storage, alter the gateway, or write into Hermes core directories. The artifact service remains usable when Hermes is stopped and can accept other agent adapters.

HTML is presented through an opaque-origin sandbox after allowlist copying. Artifact-provided JavaScript, Python, shell, and npm code is not executed.
