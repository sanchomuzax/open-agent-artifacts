# Agent integration

Open Agent Artifacts is agent-independent. An agent can use the HTTP API directly or call the `artifactctl` CLI.

## Create

```bash
artifactctl --base-url http://127.0.0.1:8765 create \
  --title "Reviewable report" \
  --kind markdown \
  --file report.md \
  --created-by agent
```

The response contains a stable artifact ID, slug, and current version ID. The agent must read the response back before presenting a link.

## Publish a new version

Full replacement:

```bash
artifactctl --base-url http://127.0.0.1:8765 publish ARTIFACT_ID \
  --file revised.md \
  --expected-current-version-id VERSION_ID \
  --change-summary "Address review feedback" \
  --created-by agent
```

Exact patch:

```bash
artifactctl --base-url http://127.0.0.1:8765 publish ARTIFACT_ID \
  --old-str "old text" \
  --new-str "new text" \
  --expected-current-version-id VERSION_ID \
  --created-by agent
```

An exact patch is rejected unless the old string occurs exactly once. A stale expected version returns `409 Conflict`; the agent must fetch the current version and decide explicitly whether to rebase or rewrite.

## Feedback

Feedback is bound to a concrete version and carries a quoted anchor. The agent should list feedback before proposing a new version:

```bash
artifactctl --base-url http://127.0.0.1:8765 feedback VERSION_ID \
  --artifact-id ARTIFACT_ID \
  --body "Please clarify this sentence." \
  --exact "quoted sentence"
```

The web UI is the preferred human path for selecting text. Submitting feedback does not authorize automatic changes. A separate, explicit agent request is required to address it.

## Hermes adapter boundary

The future Hermes adapter should call this API and return the verified link. It must not import Hermes session storage, alter the gateway, or write into Hermes core directories. The artifact service remains usable when Hermes is stopped and can later accept other agent adapters.

## API endpoints

- `GET /healthz`, `GET /readyz`
- `GET /api/artifacts`, `POST /api/artifacts`
- `GET /api/artifacts/{id}`, `GET /api/artifacts/{id}/versions`
- `POST /api/artifacts/{id}/versions`
- `GET /api/artifacts/{id}/comments`
- `GET /api/versions/{id}`, `GET /api/versions/{id}/comments`
- `POST /api/versions/{id}/comments`
- `GET /api/comments/{id}`, `GET /api/comments/{id}/events`
- `POST /api/comments/{id}/events`

When `OAA_API_TOKEN` is configured, API requests require `Authorization: Bearer <token>`. Health endpoints remain available for process supervision and do not expose content.
