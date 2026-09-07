---
name: opascope-optimize
description: Audit a process, codebase or workflow for deletion and compression. Use when asked what can be deleted, what is unnecessary, or to simplify a process. Produces recommendations only and never changes the audited target.
allowed-tools: Read Glob Grep Bash Write
metadata:
  portability:
    claude: full; evidence and counterargument passes, optional independent reviewers
    codex: full; sequential evidence and counterargument passes without team tools
---

# Optimize

Read [shared.md](shared.md) first. Save a report with kind `optimization`.
The only writes are report/task artifacts. Never edit, delete, reconfigure or
execute a proposed change in the audited target. Read-only shell checks are
permitted; do not run target scripts with unknown side effects.

Three principles: start with deletion as the hypothesis; inspect the actual
thing; make each recommendation concrete. The burden is on keeping components,
but absence of evidence is not evidence of absence.

## Inventory

Scope the target and classify code versus non-code. For code, read actual files,
search consumers and writers, and inspect history if available. For a process,
read the supplied procedure and actual observations. If observations are missing,
ask for steps, frequency, participants, outputs, last use, dependencies and what
would break if removed. Do not invent a meeting cadence or usage history.

Build a numbered map: component, purpose, last observed use, consumers, owner
or writer, size or time cost, evidence and uncertainty. Say when measurements
cover only the inspected scope.

## Load-bearing test

For every component, ask these questions in order and record the evidence:

1. Does deleting it break current behavior? Keep until that dependency is resolved.
2. When was it actually used? Inactivity is a signal to investigate, not a deadline
   after which a seasonal or recovery component becomes unnecessary.
3. Does the original problem still exist? If not, propose deletion.
4. Is it indirection without useful policy or behavior? Propose deletion or folding.
5. Does another component do the same job? Propose compression, naming the survivor.
6. Is it a safety net? Examine consequence and recovery cost. No recorded incident
   alone does not show a control is unnecessary.
7. If removed mistakenly, how hard is recovery? State the assumptions behind that
   estimate. Cheap recovery strengthens a deletion recommendation.

## Argue against deletion

Before any KILL recommendation, make the strongest evidence-based case for
keeping it. Show that argument and why it does or does not survive. Where a
consumer, consequence or recovery cost is unknown, use QUESTION with the exact
missing evidence. For ambiguous items, trace why it exists, why this form, why
it remains, why removal has not happened and why removal would matter. Use
files, history or user observations to answer; do not fill gaps with a story.

On both runtimes, complete the inventory/evidence pass first, then a separate
counterargument pass without changing the target. Independent reviewers may
perform those passes if available and authorized; sequential review is a full
implemented path and must be labeled as such. Never require a team API.

## Report

Use [references/report.md](references/report.md). Every component receives KILL,
COMPRESS, KEEP or QUESTION. Include a specific action, supporting evidence,
impact and uncertainty. Impact is prospective, conditional on applying the
recommendations. Never report projected savings as achieved. Return the report
path and the most consequential finding; the user decides what to execute.
Scale the report to the target. A few components usually need a compact table
and brief counterarguments, not a long report repeating the inventory.
