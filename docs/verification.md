---
id: verification
title: "How these skills are verified on Claude Code and Codex CLI"
owner: maintainers
covers: ["tests/**"]
---
# How these skills are verified on Claude Code and Codex CLI

Run the dependency-free automated checks from a clone:

```sh
python3 -m unittest discover -s tests -v
```

The tests install into disposable directories and compare before/after state.
They cover both runtime discovery layouts, identical linked skill contents,
idempotency, real-directory and symlink collisions, preservation of user
additions, partial-install rollback, uninstall, artifact resolution, immutable
handoffs, transcript parsing, actual proof execution and safe Git updates.
Fake workers test adverse loop behavior; these are not presented as real
runtime executions.

For opt-in model-backed checks, authenticate your installed runtimes and run:

```sh
python3 tests/live_runtime.py --runtime claude
python3 tests/live_runtime.py --runtime codex
```

These calls use the account's configured model, create standalone temporary
toy projects, install skills through the real installer and invoke every skill
by name. They consume runtime quota. Each project contains fictional notes.
The harness checks required artifacts and unchanged input hashes; inspect the
saved tool traces and artifact contents to assess behavioral quality.

Evidence paths print at startup. Logs are local working artifacts, excluded
from version control; they can contain machine paths and runtime account
metadata. Do not commit raw logs. Uninstall test projections with the printed
uninstall command. Temporary toy files are deliberately retained for inspection.

The loop-builder test scaffolds and seals a task without executing it. To test
the actual launcher, inspect its contract and run `kit.py loop run` with the
printed task path, the appropriate runtime, and a small iteration/time budget.
Check that independent proofs initially fail, the runtime creates the output,
the same proofs then pass, and rerunning needs no further runtime call.

## Archived release results

The per-release live-run results for 0.1.0, 0.3.0 and 0.4.0 were moved out of
this public document to keep it focused on how verification works. They are kept
as a local maintainer evidence record at
`work/evidence/2026-10-05-skills-release-results/results.md`.

## Publication checks

All shippable paths were swept for private identifiers, paths, configuration
names and sensitive figures, with a separate proper-noun and path review. No
such findings remained. Runtime logs, machine-specific receipts and toy files
are kept out of the Git tree, as is anything specific to the project this was
extracted from.

All implementation and prose in this package were written independently. The
license is the standard MIT text with Opascope copyright.
