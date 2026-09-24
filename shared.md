# Shared artifact and runtime contract

Read this file at the start of every skill. Resolve `kit.py` beside the loaded
SKILL.md, using its full path. It is a link to the package's shared helper. Run
commands from the user's project, not from the package directory. In commands
below, replace KIT with that actual path; do not type KIT literally.

1. Run `python3 "KIT" start`. Add `--task TASK_ID` once the task is known.
   It only reads and never creates files. Act on its lines:
   - PACKAGE names a newer version: mention it once. Update only when asked.
   - PROJECT is new: show the NEXT line as a one-line tip, then do what the
     user asked. Never hold up their task for the tip.
   - Several TASK lines and none named in this conversation: ask which one,
     or start a new task. Never pick the newest one silently.
   - Resuming a task after a break: open with a two-sentence recap of its
     latest handoff, then suggest its NEXT step once.
   For a new task, run `python3 "KIT" new "short task title"`. It prints the
   full task directory. When resuming, read that task's latest handoff and
   its read-first artifacts before proceeding.
2. Before working on a task, run start with `--task TASK_ID` and read every
   file it lists under SAVED. Treat their decisions and constraints as
   settled. Ask only about what they leave open, and never ask again for
   something they already answer.
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

## Asking

Ask only about a choice that changes the work. Use the runtime's question
tool when it works in this session. Otherwise write the question in this
shape and wait for the answer:

    Q1: <the choice, in one line>
    Why it matters: <what goes wrong if this is chosen badly>
    Recommendation: <letter>, because <reason>
    A) <option>. Good: <gain>. Cost: <loss>.
    B) <option> (recommended). Good: <gain>. Cost: <loss>.
    You're trading: <one line>

Start each label on its own line, unnumbered, as shown.
With the question tool, carry the same facts in the question and option
text and mark the recommended option. Never invent an answer because a
headless call cannot ask. Continue only work that does not depend on the
missing answer. If every relevant choice was already provided, use it
without asking again.

## Reporting

End with a status on its own line: Done, Done with concerns, Blocked, or
Needs input. Write the bare word, with no number in front. Then at most
three short lines: what changed (with the saved artifact path, if any),
what was skipped, and what to watch. A reply that only recommends a skill
still ends this way. Nothing comes after
those lines. Anything longer that the skill must hand over, such as a
question, a command block or a list of findings, goes above the status
line. A claim that something
cannot be done needs the exact error or a quick test behind it.

Keep the user's scope and authorization. Defining a target, making a plan,
auditing a process or preparing a loop does not by itself authorize executing
the target work. A request to execute does authorize the normal steps in that
workflow. Do not add an approval gate to work already authorized. Evidence
supports completion; a filename or a checked box alone does not prove quality.
