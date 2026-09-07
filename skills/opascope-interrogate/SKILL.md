---
name: opascope-interrogate
description: Surface the guesses in a task before starting. Use when asked to interrogate, grill, ask questions first, or clarify an underspecified request. Produces a short execution brief and only asks questions that would change the work.
allowed-tools: Read Glob Grep Bash Write Edit AskUserQuestion
metadata:
  portability:
    claude: full; interactive questions or numbered text
    codex: full; available question tool or numbered text with a real wait
---

# Interrogate

Read [shared.md](shared.md) first. Save the final brief with kind `brief`.

Unspecified choices tend to feel settled because you filled them in yourself.
Make those choices visible: draft the spec you are about to execute and classify
its claims. A small task can have no material guesses; do not manufacture any.

## Procedure

1. Read the request, conversation, existing task artifacts, relevant files and
   tool help. Resolve what the environment can answer before involving the user.
2. Draft goal, audience, constraints, non-goals and the first approach decisions.
   Mark each claim SAID (the user supplied it), FOUND (verified, with its source)
   or GUESSED (an assumption). A plausible inference is still GUESSED.
3. For each guess: investigate if locally answerable; keep an explicit default
   if every plausible answer leads to the same work; otherwise queue a question.
   Order scope-changing questions before dependent details and taste choices.
4. Ask up to three independent questions per round. Each asks one concrete
   decision and offers concrete hypotheses, with the recommendation first.
   Do not ask a dependent question until the answer to its parent is known.
   Follow the shared runtime path, including its text fallback and wait.
5. Package the resulting brief and proceed only as far as the request authorizes.

## Modes and stopping

Light is default: one question round, then publish the brief with remaining
assumptions explicit. If an unanswered choice blocks authorized execution, name
that blocker instead of treating the round limit as permission to guess.

Heavy applies when the user asks to be grilled or wants full interrogation.
Announce that they can say "proceed" at any point. Before another round, try to
predict each remaining answer. If confidence is sufficient for a reversible
default, record that default instead of asking. Then imagine the delivered work
disappoints the user: any failure caused by an unresolved material choice becomes
a new question. Stop when that exercise yields no material uncertainty, or the
user says proceed. Proceed never supplies missing authority for external actions.

## Brief

Keep it short: goal; what done looks like; audience; constraints; non-goals;
decisions from answers; verified facts and sources; defaults the user can veto.
Retain SAID/FOUND/GUESSED labels where they help the next session distinguish
evidence from assumptions. Publish and read back the brief, then reference its
path in the response. Zero questions is a valid result: "No material gaps."
