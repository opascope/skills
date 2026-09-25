---
name: opascope-loop-builder
description: Prepare a long unattended agent run with a spending limit, a stopping condition you can check, and progress saved to files. Use when asked for unattended work, a resumable loop, or to keep Claude Code or Codex working until a verified outcome. Builds and validates the loop; launches only when execution is requested.
allowed-tools: Read Glob Grep Bash Write Edit AskUserQuestion
metadata:
  portability:
    claude: full; portable contract with bounded claude print-mode launcher
    codex: full; same contract with bounded codex exec launcher
---

# Build an unattended Claude Code or Codex loop

Read [shared.md](shared.md) first. Keep the loop in the shared task directory.
Read [references/contract.md](references/contract.md) before writing its files.

The portable core is an outcome, immutable criteria, durable context and
independently runnable proofs. The launcher is a small foreground Python
process. Neither runtime needs native goal commands or model aliases.

## Build

1. Identify the intended outcome and what would prove it. Use prior answers and
   inspected files first. Ask only for material missing choices. A focused job
   gets one item; a longer job gets a dependency-ordered checklist. Both use
   durable artifacts so a fresh runtime call has enough context.
2. Read project instructions at every scope the work will touch. Put relevant
   complete constraint sections and the user's authority limits in SAFETY.md,
   with source paths. Include nested instructions in any worker's scope. Do not
   copy unrelated private context into a shareable loop.
3. Fill CONTEXT.md with the objective, input paths, reference facts and settled
   decisions. Use stable headings. Fill loop.json with immutable item IDs,
   descriptions, dependencies, proof argument arrays and a final behavioral
   proof. Include verifier script paths for fingerprinting and output paths
   for progress detection. No proof may rely only on the worker's done claim.
4. Write CHECKLIST.md with pending items matching the contract, NOTES.md with
   a checkpoint and decisions section, BLOCKERS.md with no unresolved entries
   unless actually blocked, and init.md with an explicit cold-read order.
5. Run the checks once to establish the baseline. Expected task failures are
   allowed; missing tools, malformed proofs or nonexistent inputs are not a
   valid baseline. Read proof code before running it. It runs with the user's
   privileges, so only trusted checks are eligible.
6. Run `python3 "KIT" loop validate "/full/task/path"`, then `loop seal` with
   that path. These validate structure and fingerprint criteria; they do not
   prove the task is complete. Never clobber an existing sealed/live loop. Reuse
   it to resume; create a new task for a reviewed change in scope or criteria.

## Launch and resume

If asked only to build a loop, deliver the artifact path, baseline findings and
the concrete launch command, then stop. If the user also asked to run it, use
the appropriate command, within the already authorized scope:

```text
python3 "KIT" loop run "/full/task/path" --runtime claude --steps 10 --seconds 300
python3 "KIT" loop run "/full/task/path" --runtime codex --steps 10 --seconds 300
```

Replace KIT and the task path with actual resolved paths. Select the installed
runtime requested by the user; leave their model selection intact. The runner
starts fresh calls that read artifacts before each item, so it also resumes
after context loss or a runtime switch. Inspect checkpoints and blockers before
a subsequent explicit run. Clear a blocker only when its unblock condition is
verified, preserving its history in NOTES.md.

## Completion discipline

The worker updates deterministic item status only after showing actual proof.
It never changes success criteria or verifier files. An independent reviewer
must handle a judgment clause; if unavailable, record the missing review as a
blocker, not a self-issued PASS. A reviewed scope amendment becomes a new sealed
task linked to the old one, so removed requirements remain visible.

The runner checks all item proofs and the final proof itself. It detects
criterion drift and stops for blockers, runtime failure, timeout, unchanged
progress or budget exhaustion. None of those is completion. Exit 0 alone means
all proofs passed. Read the completion artifact and inspect the resulting work
before reporting success; a poor proof can still certify the wrong outcome.

Explain these limits: the launcher is not an OS sandbox or a scheduler; no
automatic wakeup occurs. Runtime permissions may block an unattended action,
and that is an actionable blocker. Do not bypass them to keep a loop alive.
