# Changelog

## 0.2.1 — 2026-09-16

- Ensure HTML preview iframes do not intercept catalog-card activation.
- Keep the live catalog and canonical project-description artifact aligned with the patch release.

## 0.2.0 — 2026-09-15

- Replace the split review shell with a full-width artifact catalog and separate artifact workspace.
- Add grid/list catalog browsing, scope tabs, search, grouped pinned items, visual previews, and stateful Home/Back navigation.
- Render Markdown as a real document and display HTML visually inside an opaque-origin sandbox without executing artifact scripts.
- Add inline selection comments, persistent highlights, comment visibility controls, version history, immutable restore, rename, and duplicate APIs.
- Add principal/preferences groundwork, snapshot cursors, legacy database migration coverage, and Playwright reference UX acceptance.

## 0.1.10 — 2026-09-15

- Make the project description a canonical pinned HTML artifact synced at service startup.
- Make artifact opening a separate workspace screen with a reliable home/back path.
- Add list/card preview acceptance coverage and changelog-backed release-description validation.

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
