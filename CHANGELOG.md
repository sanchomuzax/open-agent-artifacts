# Changelog

## 0.1.9 — 2026-09-15

- Add list/card catalog views with content previews.
- Add persisted pin/unpin state and pinned-first catalog ordering.
- Add in-place version publishing, comment visibility controls, and anchored highlights.

## 0.1.8 — 2026-09-15

- Route every artifact request through the primary live workspace.
- Clarify that GitHub is the source-code extra, not the artifact destination.

## 0.1.7 — 2026-09-15

- Add a first-class project overview link to the main artifact workspace.
- Keep the live workspace and bilingual project description connected.

## 0.1.6 — 2026-09-15

- Add the public bilingual project description with an EN/HU switch.
- Enforce project-description version synchronization in validation and CI.

## 0.1.5 — 2026-09-15

- Fix installed deployments to resolve the static web assets from the working directory.
- Pass the explicit static asset directory in the hardened systemd template.

## 0.1.4 — 2026-09-15

- Add an in-browser line diff for comparing an older version with the current version.
- Keep diff rendering bounded for large documents.

## 0.1.3 — 2026-09-15

- Add a hardened loopback-bound systemd deployment template.
- Add the initial Hermes adapter contract and deployment runbook.

## 0.1.2 — 2026-09-15

- Add validated SQLite backup and atomic restore helpers.
- Add backup/restore command-line wrappers and operations documentation.

## 0.1.1 — 2026-09-15

- Add the local HTTP API around immutable artifact storage.
- Add the `artifactctl` client for agent integrations.
- Add the first mobile catalog and safe, text-only source view.
- Add version-anchored feedback and comment status actions.
- Add the pre-publication privacy scanner and automated release workflow.

## 0.1.0 — 2026-09-15

- Add SQLite persistence for artifacts, immutable versions, exact patches, and audited feedback events.
