# Verification

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

## 0.1.0 release results

Verified on Linux on 2026-09-07 with Claude Code 2.1.263 and Codex CLI 0.153.4.
macOS and WSL are supported by the implementation but were not exercised on
separate machines in this pass.

- 34 standard-library automated tests passed.
- A fresh remote clone completed the interactive installer in a real terminal,
  installed both runtime layouts, and passed rerun/idempotency checks. Uninstall
  restored the empty installation base exactly. Existing-file preservation and
  partial failure rollback were separately tested.
- All six skills plus the router completed real calls in both runtimes. Each
  skill was invoked by its installed name; tool traces and resulting artifacts
  were inspected, not just frontmatter.

| Skill | Observed outcome on both runtimes |
|---|---|
| define-done | A falsifiable retrieval-and-preservation objective; no implementation. |
| interrogate | A saved brief separating supplied choices, verified facts and defaults; no needless questions when choices were complete. |
| planning | A saved plan with dependencies, byte-preservation checks and explicitly labeled sequential review; implementation stayed pending. |
| optimize | A deletion/compression report distinguishing unique notes from duplicate index entries; all audited input hashes unchanged. |
| session-handoff | Verified pickup notes, explicit next action, existing-file pointers and a successful resume/read-back. |
| loop-builder | A one-item greeting contract, context, constraints, checklist, checkpoint and blockers; structure validated and criteria sealed. |
| router | Recommended define-done for an outcome-framing request without executing unrelated workflows. |

The prepared loops were then launched through the actual foreground adapters.
Independent item and final proofs failed before work and passed afterward.
The output was exactly six bytes, `hello` followed by LF; original note hashes
were preserved. A second run recognized completion without launching a worker.

One Codex worker incorrectly interpreted an escaped display of the proof as a
literal backslash and stopped. Inspecting the decoded byte values disproved that
claim. The correction was recorded in the checkpoint, the blocker cleared, and
the same sealed criteria resumed successfully. This is a real limitation of
model judgment, not a reason to soften the proof or equate a blocker with done.

The first nested toy-project test also inherited its parent project's artifact
root. Moving the harness to standalone temporary projects corrected the test
boundary. Additional fresh-clone handoff runs exercised both runtimes with the
final root and preservation instructions.

The usage command was run against actual local transcripts in addition to
fixtures. That exposed built-in command false positives, which were corrected
with catalogue matching and regression tests. No personal rankings or raw
transcripts are published.

## Publication checks

All shippable paths were swept for private identifiers, paths, configuration
names and sensitive figures, with a separate proper-noun/path review. No such
findings remained. Runtime logs, machine-specific receipts and toy artifacts
are excluded from the Git tree. Source-specific examples, integration calls
and historical ledgers were not included in the package.

Whole-file hash comparison against 677 packaging-reference markdown/script
files found no verbatim matches. No implementation or prose was copied from
that project. Its general flat-link, router and install/update patterns informed
the design. The license here is the standard MIT text with Opascope copyright.
