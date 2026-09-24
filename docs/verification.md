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

## 0.4.0 release results

### Saved work carries forward

Verified on Linux on 2026-09-24 with Claude Code 2.1.281 (model claude-opus-5-5)
and Codex CLI 0.155.1 (its default model).

- 69 standard-library automated tests passed.
- Every skill plus the router ran for real on both runtimes. All 37 contract
  checks held on each: 74 passing evaluations, with every fixture file
  unchanged afterwards.
- New check `carries-the-saved-brief`: the planning run continues a task whose
  saved brief asks for `sources.txt`, a file the request never names. The
  checker skips seeded files, so only the skill's own plan can pass it. It
  passed on both runtimes.

This proves a skill reads what the task already saved and carries it into its
own work, rather than starting again from the request alone.

The first codex round found two skills writing honest output in a shape their
existing checks could not read. A brief put a stated file name on a line marked
GUESSED, and an audit ruled on the planted file under a nickname instead of its
path. Both skills now say how to write that line. Neither check was changed.

### One way to ask and report

Verified on Linux on 2026-09-24 with Claude Code 2.1.281 (model claude-opus-5-5)
and Codex CLI 0.155.1 (its default model).

- 69 standard-library automated tests passed.
- Every skill plus the router ran for real on both runtimes. All 45 contract
  checks held on each: 90 passing evaluations, with every fixture file
  unchanged afterwards.
- New check `closes-with-a-status`, on every contract: the reply ends on Done,
  Done with concerns, Blocked or Needs input, with at most three lines after
  it. It held for every skill on both runtimes.
- New check `recommends-an-answer`: interrogate now has one open choice, and
  its question must state what is at stake and recommend an answer, in that
  order. It held on both runtimes.

Together they prove a reader can tell at a glance whether a run finished, and
that a question hands back a recommendation, not just a choice.

The runs also found three things to fix. Codex squeezed its question onto one
line because the test prompt asked for a brief answer. The prompt now asks to
keep a question block whole. A plan wrote its actions as prose with no `Do:`
label, so planning now names the labels. Two older checks failed honest work:
one read "topic-index" inside a task ID as a finished index, and one failed a
brief that took the stated file name as given and guessed only its layout.
Both checks were narrowed on the owner's ruling. Each honest output is now a
test fixture, and the wrong answers they were written for still fail.

## 0.3.0 release results

Verified on Linux on 2026-09-07 with Claude Code 2.1.263 and Codex CLI 0.153.4.

- 56 standard-library automated tests passed, on Python 3.9 and 3.12 in CI.
- Every skill plus the router ran for real on both runtimes. All 36 contract
  checks held on each: 72 passing evaluations, with every fixture file
  unchanged afterwards.

What the runs actually caught is worth stating plainly, because it is the
opposite of what these runs are usually reported to show. Across three rounds on
both runtimes, no skill was found breaking its promise. Eight CHECKS were found
failing work that kept it.

Each of the eight had the same shape. A pattern written to catch a claim also
caught the denial of that claim, or caught a synonym the runtime had every right
to use:

- A plan recorded `Mode: sequential self-review, not independent`, which is the
  disclosure the skill instructs it to write. The check read the word
  "independent" and called it a claim of independent review.
- A handoff wrote `No topic index or index plan has been created`, correctly
  reporting that nothing had started. The check read the completion word and
  ignored the "No" in front of it.
- A brief labelled a stated file name SAID and only the unstated detail GUESSED,
  exactly as promised. The check failed it for naming both on one line.
- A plan wrote `Action:` where the template says `Do:`. Same meaning, and the
  promise is about every step having an action, not about a label.
- A report ruled QUESTION on the planted waste, with evidence and two open
  questions about who reads it. Asking is looking; the check wanted a deletion.
- A checklist said `Require exit 0 before marking done`, which is prose telling a
  worker how to verify. The check read it as a proof that cannot fail.

None of these appeared in offline testing. Fabricated artifacts prove a check can
fail; only a real run proves it fails for the right reason. Every one of those
six outputs is now a fixture in `tests/test_gates.py` that the checks must keep
passing, alongside the wrong answers they must keep catching.

## 0.1.0 release results

Verified on Linux on 2026-09-07 with Claude Code 2.1.263 and Codex CLI 0.153.4.
macOS and WSL are supported by the implementation but were not exercised on
separate machines in this pass.

- 35 standard-library automated tests passed.
- A fresh remote clone completed the interactive installer in a real terminal,
  installed both runtime layouts, and passed rerun/idempotency checks. Uninstall
  restored the empty installation base exactly. Existing-file preservation and
  partial failure rollback were separately tested.
- Every skill plus the router completed real calls in both runtimes. Each
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

The usage command was run against real local transcripts as well as fixtures.
No personal rankings or raw transcripts are published.

## Publication checks

All shippable paths were swept for private identifiers, paths, configuration
names and sensitive figures, with a separate proper-noun and path review. No
such findings remained. Runtime logs, machine-specific receipts and toy files
are kept out of the Git tree, as is anything specific to the project this was
extracted from.

All implementation and prose in this package were written independently. The
license is the standard MIT text with Opascope copyright.
