# Development and operations runbook

## Local development

```bash
uv sync --extra dev
uv run pytest -v
node --check web/app.js
uv run python scripts/check_publication.py --root .
```

The service binds to `127.0.0.1` by default and stores runtime data outside the repository:

```bash
oaa-server --db ~/.local/share/open-agent-artifacts/artifacts.db --host 127.0.0.1 --port 8765
```

Production-like runs must set an explicit target identity outside the
repository:

```text
OAA_INSTANCE_ID=production
OAA_STORAGE_CLASS=persistent
OAA_PUBLIC_URL=https://artifacts.example.invalid
```

Use `artifactctl doctor` before writes and `artifactctl create --verify` when
creating a deliverable. Use `artifactctl smoke` only as an explicit synthetic
check; it archives and verifies cleanup of its own test artifact.

For a protected deployment, set `OAA_API_TOKEN` outside the repository. Never place the token in this file or in a shell history that is committed.

## Backup and restore

Use the SQLite online backup API rather than copying a live database file:

```bash
uv run python scripts/backup.py \
  --source ~/.local/share/open-agent-artifacts/artifacts.db \
  --destination /secure/backup/location/artifacts.db

uv run python scripts/restore.py \
  --backup /secure/backup/location/artifacts.db \
  --destination /tmp/artifacts-restored.db
```

Restore refuses to overwrite an existing destination unless `--overwrite` is explicit. Both operations validate `quick_check`, foreign keys, and the expected schema. Test a restore in a separate directory before using it for a service replacement.

## Private access

The application itself should remain loopback-bound. Put an approved, authenticated Tailscale access layer in front of it; do not bind the application broadly merely to make a remote browser work. Do not use Tailscale Funnel for private artifacts.

Before configuring a real route, inspect the host's current Serve configuration and preserve unrelated routes. Record the actual hostname only in private operational notes, never in the public repository.

## Release

Product changes are tested by GitHub Actions. A code-changing push to `main` is eligible for a release only when the test, publication, and browser-bundle gates pass. The release workflow reads the package version, creates an annotated tag, archives tracked files, publishes an English GitHub Release, and includes a SHA-256 file.

A release is not an automatic production deployment. Deployment requires a separate, reviewed operator action.

## Failure handling

- A failed integrity check is a failed operation, not an empty successful result.
- A stale version returns conflict and must not be silently overwritten.
- A preview or API error must not replace the last known good artifact version.
- Never delete runtime data as a cleanup shortcut.
