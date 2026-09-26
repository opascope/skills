---
name: openrouter
description: Call other models through OpenRouter with one small script. Use for web research with citations, current facts, a second opinion from another model, embeddings, a closed-choice decision, "which model should answer this", model prices or context sizes, or credit left on the key. When no model is named, Jev picks one per request, and every call reports the model that answered and what it cost.
allowed-tools: Read Glob Grep Bash Write Edit AskUserQuestion
metadata:
  portability:
    claude: full; runs the script with Bash
    codex: full; runs the script with the shell
---

# Ask another model through OpenRouter

Read [shared.md](shared.md) first. Save each call's receipt with kind `receipt`.

The script sits beside this file. Run it by its full path:
`python3 "<this skill's directory>/scripts/openrouter.py" <verb> ...`. Resolve the
directory from where you loaded this SKILL.md; do not rely on a variable for it.

## When to use it, and when not

Use it when the answer needs something you do not have: the live web, a different
model's view, a vector, a decision limited to fixed options, or catalog facts. Do not
use it for ordinary reasoning or writing you can do yourself. A second model is a
cost, not a ritual.

## The key

Networked verbs need `OPENROUTER_API_KEY` in the environment. Create one at
https://openrouter.ai/keys. `models` works without it. Never print the key, put it in
a command line you show, or save it in any file.

## The verbs

```
openrouter.py chat "<prompt>" [--role R | --model ID] [--system S] [--json]
                              [--max-tokens N] [--effort none|low|medium|high] [--image PATH]
openrouter.py research "<question>" [--deep]
openrouter.py pick "<representative prompt>"
openrouter.py decide --state FILE|- --questions FILE
openrouter.py embed "<text>" [--json]
openrouter.py models [list [--search Q] [--newest] | check]
openrouter.py key
```

Every networked verb takes `--json-out` for one JSON object, and `--receipt FILE` to
write the trailer and the generation id to a file.

## How the model is chosen

First match wins:

1. `--model ID`, when the user named a model.
2. `--role R`, when the user named a need. The roles are in
   [references/choosing-a-model.md](references/choosing-a-model.md).
3. The verb's own need: `research` uses `sonar` (`--deep` uses `sonar-pro`), `embed`
   uses `embed`, `decide` uses `decision`.
4. Otherwise `auto`: Jev reads the request and picks the model and effort for it.

The answer goes to stdout. One trailer line goes to stderr:

`model: <the model that answered> | chosen by: <how> | cost: $<n> | tokens: <in>/<out>`

Report the model from the trailer, never the router or the role. It is the one that
answered. Report the cost the trailer states; if it says `unknown`, say so and do not
guess. [references/auto-routing.md](references/auto-routing.md) has what was measured.

## Keep the receipt

A made-up model name and cost look just like real ones. So save proof that the script
ran. Add `--receipt FILE` to each call. Then run
`python3 "KIT" save TASK_ID receipt < FILE`. The receipt holds the trailer and the
OpenRouter generation id (it starts `gen-`), and never the key.

## Batches

The router can pick a different model for each row. For a batch, run `pick` once on a
typical row. Then pass the model it prints as `--model` for every row, so the batch is
consistent.

## Research

`research` asks Perplexity Sonar and prints numbered sources under the answer. Use
`--deep` for a question that needs several sources weighed. Cite the sources it
returns; do not add sources it did not return.

## Gotchas

- An empty reply with a small budget means the model spent it thinking. The script
  retries once with a bigger budget, then names the model and budget. Raise
  `--max-tokens`.
- When the call ran on the account's own provider key, OpenRouter's `cost` is 0. The
  script then reports the upstream cost, which is the real one.
- An account with a strict data policy can refuse a model that is in the catalog. The
  script says it is the privacy setting, not a missing model.
- Models expire. `models check` flags a role whose model is gone or past its date.
- For decisions, write closed questions. Jev picks labels; count and compute in code.

Follow shared.md's Asking and Reporting sections.
