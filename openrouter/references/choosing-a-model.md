# Choosing a model

You rarely choose a model. You choose a role, and `scripts/roles.json` names the model
behind it. That file is the only place a model is written. `models` prints what each
role points at today.

## The roles

| Role | The need it names | Follows latest or pinned |
|---|---|---|
| `auto` | no model named: Jev picks per request | the router |
| `fast` | everyday calls: extraction, classifying, short answers | follows latest |
| `frontier` | hard calls and second opinions | follows latest |
| `writer` | long prose | follows latest |
| `sonar` | web research with citations | pinned |
| `sonar-pro` | deeper web research with citations | pinned |
| `embed` | text embeddings for search | pinned |
| `decision` | closed-choice answers with confidence | follows Jev versions |

**Follows latest** means the role points at an OpenRouter `~vendor/family-latest`
alias. A new model in that family is used with no edit.

**Pinned** means the role names one exact model and says why. Sonar names are
products, not versions. An embedding model is pinned because a vector index only
works with the model that built it, so a change means rebuilding the index.

To move one role for a call or a quick rollback, set `OPENROUTER_MODEL_<ROLE>`, for
example `OPENROUTER_MODEL_FAST`. `--model` still wins over it.

## Effort

Reasoning effort is a dial on the call, not a role. Pass
`--effort none|low|medium|high`. Raise it for a hard judgment. Lower it when a model
thinks too long on a simple task and runs out of budget.

## Looking at the catalog

```
openrouter.py models                      # each role and its model
openrouter.py models list --search sonar  # find models by name
openrouter.py models list --newest        # newest first
openrouter.py models check                # every role still exists and has not expired
```

None of these need a key.

## When to pass --model

Only for a real one-off: a model the user asked for, a result you must reproduce on
one model, the model `pick` printed for a batch, or a candidate you are testing. If you
keep passing the same `--model`, change the role in `roles.json` instead.
