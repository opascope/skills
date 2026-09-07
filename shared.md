# Shared artifact and runtime contract

Read this file at the start of every skill. Resolve `kit.py` beside the loaded
SKILL.md, using its full path. It is a link to the package's shared helper. Run
commands from the user's project, not from the package directory. In commands
below, replace KIT with that actual path; do not type KIT literally.

1. Once per session, run `python3 "KIT" update --offline --check`. This reads
   locally known release tags only. A failure must not block the user's task.
   Mention an available update once; run a network update only when requested.
2. Run `python3 "KIT" resume` to inspect existing task identities and handoffs.
   Reuse the task ID already established in the conversation. For a new task, run
   `python3 "KIT" new "short task title"`. It prints the full task directory.
   Do not silently choose the newest unrelated task. When resuming, read that
   task's latest handoff and its read-first artifacts before proceeding.
3. Save each skill's artifact in that task directory. Use the runtime's file
   editing tool for drafts. For an immutable final artifact, pass the draft's
   contents on stdin to `python3 "KIT" save TASK_ID KIND`. Example:
   `python3 "KIT" save notes-ab12cd34ef brief < "/full/task/path/brief-draft.md"`.
   Use the actual task ID and path printed by the helper. `save` prints the
   new artifact path. Read it back before reporting success.

Every skill in this package shares this task directory. Never invent another state root.
An optional project `.opascope-skills.json` supports one `artifact_dir` key;
zero configuration uses `.opascope-work`. `python3 "KIT" path` resolves it
without creating it. Do not edit project ignore rules or startup instructions
as a side effect of a skill invocation.

Claude Code: use Read, Glob, Grep, Bash and the file tools when available.
Codex: use its shell and patch tools for the same reads and writes. Both use
the identical Python helper. Tool names in frontmatter describe capabilities;
they do not override host permissions. Audit targets remain read-only under
optimize, even though writing a report in the artifact directory is allowed.

For a material unanswered question, use the runtime's interactive question tool
if it works in this session. Otherwise ask numbered plain-text questions with
lettered options, mark a recommended choice, and wait for the user's answer.
Never invent an answer because a headless call cannot ask. Continue only work
that is independent of the missing answer. If all relevant choices were already
provided, use them without asking again.

Keep the user's scope and authorization. Defining a target, making a plan,
auditing a process or preparing a loop does not by itself authorize executing
the target work. A request to execute does authorize the normal steps in that
workflow. Do not add an approval gate to work already authorized. Evidence
supports completion; a filename or a checked box alone does not prove quality.
