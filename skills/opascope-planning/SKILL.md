---
name: opascope-planning
description: Write an executable plan whose steps each have a pass/fail check. Use for nontrivial tasks with dependencies, multiple sources or tools, or a deliverable, and whenever asked to plan or break work down. Supports code, research and ordinary workflows.
allowed-tools: Read Glob Grep Bash Write Edit AskUserQuestion Agent
metadata:
  portability:
    claude: full; independent reviewer when available, otherwise sequential review
    codex: full; independent reviewer when available, otherwise recorded sequential review
---

# Plan agent work with a check on every step

Read [shared.md](shared.md) first. Save the plan with kind `plan`.

A step without a check is an assumption about success. Write a plan before
nontrivial execution, scaled to the task. A direct lookup or obvious one-file
edit usually needs no plan unless requested.

## Shape the work

Read actual inputs, local instructions, relevant prior decisions and tool help.
State the desired outcome and the authorized scope. Separate code, research,
workflow and deliverable concerns where their checks differ. If the plan spans
several independently deliverable architectural changes, split it into linked
plans with explicit dependencies; do not build a large plan just to review it.

For a few straightforward steps, a short plan and one self-check suffice. For
a substantial plan, review the highest-risk dimension. For a cross-cutting plan,
review feasibility, completeness and scope separately. These are judgment calls,
not a fixed file-count threshold.

## Write

Use [references/plan.md](references/plan.md). Include goal, non-goals, constraints,
actual input paths, command syntax, outputs and a final end-to-end check.
Inline the decisions and criteria needed to execute cold. Each step names:

- One action, with concrete objects and an owner if someone else must act.
- Dependencies and conditional branches when outcomes affect the next step.
- A binary verification and the evidence location.
- Status: pending, in_progress, done, failed or blocked.

Counts can verify coverage; they do not verify accuracy. For research, track
claim sources and contradictory evidence. For documents, define required
content and a quality rubric. Mark a human judgment check pending until that
reviewer actually supplies the verdict.

## Review on both runtimes

When an independent reviewer is available and authorized, give them the plan,
raw inputs and the most relevant review role. They return PASS or specific
findings. Use independent parallel reviewers only when warranted and supported.

Otherwise perform three sequential passes yourself: (1) walk commands and
dependencies against the environment; (2) map every requirement to a step and
check; (3) try to remove work and identify authorization or scope gaps. Record
findings, revisions and "sequential self-review, not independent". This path
runs on Codex and Claude Code without any team tool. It does not waive a user
requirement for independent review. Such a requirement remains pending.

Revise concrete defects. After two unproductive review cycles, expose the
unresolved decision instead of quietly weakening the plan. A planning-only
request ends with the saved plan. If execution was requested, existing
authorization carries through; do not require a ceremonial approval.

## Execute when asked

Keep step status current. Run the check after the relevant action and record
what happened. A failure blocks dependent steps, while unrelated work may
continue. Investigate the failure, change the approach when evidence warrants,
and report a real blocker if missing authority or information prevents progress.

Before completion, check coverage and run the end-to-end proof. Repeat earlier
checks only if later changes could invalidate them. Report actual pass/fail
results and any outstanding judgment review. Name the files the recipient
should open; do not make them sort through supporting evidence to find the work.
