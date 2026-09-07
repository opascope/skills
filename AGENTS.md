# Contributing to the Opascope work-process skills

Keep this package domain-independent and dependency-free: markdown and Python's
standard library only. Read ARCHITECTURE.md before changing artifact semantics.
Canonical skill instructions live in skills/, never in generated copies.

Use feature branches and pull requests to main. Run
`python3 -m unittest discover -s tests -v` before merging. Runtime smoke tests are
separate, opt-in checks using installed Claude Code and Codex accounts.

Never commit real user transcripts, credentials, private project details, or local
machine paths. Use fictional filesystem fixtures. Preserve unowned install paths.
The optimize skill may write a report, but must never change its audit target.
Loop completion needs independent command evidence, never just a worker's claim.
