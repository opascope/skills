# Handoff shape

```markdown
# Handoff: task title
Project root:
Task ID:
Current state: one sentence naming the exact cursor

## Completed
Output, path and observed proof for each completed result.

## In flight and next action
Partial state, last successful check and the next concrete command or decision.
Say explicitly if no work is mid-execution.

## Files touched
Only changes attributable to this task, with their purpose.
Identify unrelated existing changes when they affect pickup.

## Blocked
Each blocker, its effect, and the evidence or person that can unblock it.
Write "None" when appropriate.

## Decisions and authority
Settled decisions, open choices, defaults and the user's authorized scope.

## Read first
A short ordered list of verified paths, enough to continue without the chat.

## Verification limits
What was not run, what was inferred, and what disk state needs rechecking.
```

Preserve the reason for a choice when forgetting it would cause rework. Do not
paste the conversation or logs wholesale. A handoff is a working instruction
for the next reader, not a celebratory summary of activity.
