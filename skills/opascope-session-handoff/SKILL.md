---
name: opascope-session-handoff
description: Preserve verified progress so a new session can resume cold. Use when asked to write a handoff, log progress, leave pickup notes, or resume a previous task. Records completed work, the exact next action, decisions, blockers and read-first artifacts.
allowed-tools: Read Glob Grep Bash Write Edit
metadata:
  portability:
    claude: full; shared Python artifact publication and resume
    codex: full; identical artifact publication and resume
---

# Session handoff

Read [shared.md](shared.md) first. Publish with kind `handoff`. This single
artifact is the pickup contract; no runtime-specific state capture is needed.

## Write a handoff

1. Establish the task ID from the conversation or `kit.py resume`. Read its
   previous handoff and artifacts. Cross-check conversation claims against the
   current files, command results, and git status/diff when this is a git project.
   In a non-git folder, inspect the relevant files and their actual contents.
2. Distinguish verified work, unverified recollections and unrelated existing
   changes. Never claim every dirty file belongs to this session. If compaction
   erased the rationale, say which facts were recovered from disk and what the
   next session must verify.
3. Write a compact handoff using [references/handoff.md](references/handoff.md).
   Its core is the exact next action, including the current partial state. Record
   completed output with proof, in-flight work, files touched, locked decisions,
   open questions and each blocker's explicit unblock condition.
4. Supply a short read-first list sufficient for a cold reader. Use project-root
   relative paths and identify the root; use absolute paths for external files.
   Check that existing pointers resolve. Mark planned files as future outputs.
5. Publish through `kit.py save TASK_ID handoff`, which creates a unique file and
   preserves prior handoffs. Read it back and run `kit.py resume TASK_ID` to prove
   the pickup command returns this handoff. Report its path and the next action.

## Resume a task

Run `kit.py resume` to list task identities. Use the task the user named; ask
only if multiple matches make the intended task ambiguous. Run
`kit.py resume TASK_ID`, read the read-first artifacts, then verify the
checkpoint against current disk state. A handoff is evidence to check, not
permission to execute every listed idea. State what is complete, what is next
and what remains blocked. Continue the originally authorized task when asked.

Do not restart completed work, reopen settled choices without new evidence, or
claim automatic startup pickup. A new session must invoke the router/handoff
skill or be told to read the returned handoff path.
