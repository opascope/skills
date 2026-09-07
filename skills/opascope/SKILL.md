---
name: opascope
description: Choose a work-process skill when unsure how to start, clarify a task, define completion, plan work, audit for deletion, prepare unattended work, or resume a session. Also handles package update and local usage-ranking requests.
allowed-tools: Read Glob Grep Bash Write Edit AskUserQuestion Skill
metadata:
  portability:
    claude: full; native skill invocation or direct reading
    codex: full; dollar-name invocation or direct reading
---

# Opascope Skills

Read [shared.md](shared.md) first. Route to the smallest useful workflow.

| Need | Skill |
|---|---|
| Expose guesses before starting | opascope-interrogate |
| State what solved means | opascope-define-done |
| Turn scope into verified steps | opascope-planning |
| Leave or resume cold-session context | opascope-session-handoff |
| Find what to delete or compress | opascope-optimize |
| Prepare bounded unattended execution | opascope-loop-builder |

For a vague request, identify what is actually missing: intent, outcome, path,
continuity, necessity or persistence. Recommend one skill and explain why in
one sentence. If the user asked for the work, load that skill and execute its
instructions. If they only asked which skill fits, stop after the recommendation.
Do not run the entire suite as a ritual.

Claude Code can invoke a skill by its slash command or Skill tool. Codex can
invoke by its dollar-name or read the SKILL.md from the installed skill
directory. If an invocation tool is absent, directly read the chosen SKILL.md
beside this skill's installed directory and follow it. Canonical source paths
are also available by resolving this directory's kit.py link to the package.

## Package commands

For usage measurement, run `python3 "KIT" usage` or the user-supplied transcript
paths. Explain that it counts explicit names only and never transmits logs.
Read the package's docs/usage.md for supported records and counting limits.

For updates, run `python3 "KIT" update --check` and report the result. When the
user requests installation of an update, locate the receipt at the installation
base (home or project) and run `python3 "KIT" update --base "/installation/base"`.
Repeat --base for multiple known installations. Do not stash, reset or overwrite
local changes to make an update succeed. On failure, report the exact condition.
This is the package's upgrade workflow; no separate upgrade skill is installed.

For pickup, `python3 "KIT" resume` lists tasks without inventing a global current
task. Select the intended task and route to opascope-session-handoff's resume path.
