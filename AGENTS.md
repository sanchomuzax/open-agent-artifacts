# Open Agent Artifacts — contributor guidance

This repository contains the public product. Keep it portable, English-language, and free of personal or operational data.

## Boundaries

- Never commit secrets, tokens, cookies, private keys, runtime databases, backups, real user content, personal paths, private URLs, tailnet hostnames, or operational logs.
- Use synthetic fixtures and placeholders in public tests and documentation.
- Keep the private companion project outside this repository.
- Do not modify Hermes core, gateway, scheduler, or session storage from this project.
- Do not run a generated artifact with access to admin credentials or agent tools.

## Development

- Follow test-driven development: write a focused failing test, observe the expected failure, implement the smallest change, then run regression tests.
- Test negative cases and the real entry point, not only internal helpers.
- Use explicit file lists for Git staging; never use broad staging for a release checkpoint.
- Use Conventional Commits in English: `feat(scope): ...`, `fix(scope): ...`, `chore(scope): ...`.
- A release requires passing CI, publication checks, and an independent review.
- Record any behavior that depends on the host environment and verify it in that environment.

## Scope of the first release

The first release supports static artifact content, immutable versions, anchored feedback, catalog browsing, and a local API. It does not execute arbitrary JavaScript, Python, shell commands, or npm installs.
