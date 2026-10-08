# Contributing to the Opascope work-process skills

Keep this package domain-independent and dependency-free: markdown and Python's
standard library only. Only `openrouter` and `jevify` call outside models; they need
`OPENROUTER_API_KEY`, show each call's cost, and stop cleanly without it. Read ARCHITECTURE.md before changing artifact semantics.
Canonical skill instructions live in the skill folders at the repo root, never in generated copies.

Use feature branches and pull requests to main. Every PR adds one plain line
under `## [Unreleased]` in CHANGELOG.md, or says `no user-visible change`; releases
follow docs/runbooks/cutting-release.md. Run
`python3 -m unittest discover -s tests -v` before merging. Runtime smoke tests are
separate, opt-in checks using installed Claude Code and Codex accounts.

Never commit real user transcripts, credentials, private project details, or local
machine paths. Use fictional filesystem fixtures. Preserve unowned install paths.
The optimize skill may write a report, but must never change its audit target.
Loop completion needs independent command evidence, never just a worker's claim.

<!-- manage-docs:router -->
## Docs and records

- Docs map: [docs/README.md](docs/README.md), one line per doc with its receipt status.
- Where does a new file go? `python3 tools/docs/docs.py where "<what it is>"`.
- Before a PR: `python3 tools/docs/docs.py check`.
<!-- /manage-docs:router -->
