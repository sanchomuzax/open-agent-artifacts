# Deployment templates

These files are templates only. They are not installed automatically.

## Install the application

Use a dedicated service account and a release archive. Keep the runtime data outside the repository:

```bash
sudo useradd --system --home /var/lib/open-agent-artifacts --shell /usr/sbin/nologin agent-artifacts
sudo install -d -o agent-artifacts -g agent-artifacts /var/lib/open-agent-artifacts
sudo install -d -o root -g root /opt/open-agent-artifacts
uv sync --extra dev
```

The production installation should use the pinned release artifact and its published SHA-256 file, not an arbitrary working tree.

## Configure the service

Copy `open-agent-artifacts.service` to the systemd unit directory only after reviewing paths and permissions. Put the optional API token in `/etc/open-agent-artifacts/env` with mode `0600`; it must not be committed.

```bash
sudo install -m 0644 open-agent-artifacts.service /etc/systemd/system/open-agent-artifacts.service
sudo systemctl daemon-reload
sudo systemctl enable --now open-agent-artifacts.service
curl --fail http://127.0.0.1:8765/readyz
```

`ProtectHome=true` is intentional. The service must not read a user's home directory. Runtime write access is restricted to `/var/lib/open-agent-artifacts`.

## Tailscale access

The service remains loopback-bound. After local health checks pass, an operator may create a private Tailscale Serve route to `http://127.0.0.1:8765`. Review the current Serve configuration first and preserve unrelated routes. Do not use Funnel. Record the real hostname only in private operational notes.

After the route is approved, test the complete user path from a remote tailnet device: open catalog → open artifact → select text → save feedback → reload → verify feedback. A local curl is not sufficient evidence.

## Rollback

1. Stop the service only after recording its current status.
2. Restore the previous application release, not the database, when the issue is code-only.
3. Restore a database only through the validated restore script into a separate path first.
4. Run `/readyz` and read-only version/history checks.
5. Re-run the remote user path.

No deployment command in this directory changes an existing Hermes service, gateway, scheduler, or session database.
