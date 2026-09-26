# How the auto role behaves

When you name no model and no role, the script sends the call to the `auto` role. That is
the Jev router on OpenRouter. It reads each request and picks the model and the reasoning
effort for it. The trailer then names the model that actually answered, never the router.

## What we measured on 2026-09-26

Seven calls through the router, from the script in this folder, then two more for the retry
case below. Costs are what each
response stated. Some calls ran on the account's own provider key, so the real cost came
from `upstream_inference_cost`, not from `cost`.

| Call | Model that answered | Cost | Cost field | Finish |
|---|---|---|---|---|
| "What is 17 times 23?" | openai/gpt-6-luna | $0.0000144 | upstream_inference_cost | stop |
| A short proof that there are infinitely many primes | openai/gpt-6-luna | $0.0000526 | upstream_inference_cost | stop |
| `--json`, first try | openai/gpt-6-sol | $0.000216 | upstream_inference_cost | stop |
| `--json`, second try | google/gemini-3.8-flash | $0.00006225 | cost | stop |
| `--json`, third try | google/gemini-3.8-flash | $0.00006225 | cost | stop |
| `--image` with a small red square | openai/gpt-6-luna | $0.000012 | upstream_inference_cost | stop |
| 16 tokens on a question that invites reasoning | deepseek/deepseek-v4.1-flash | $0.0000324 | cost | length |

What this showed:

- **The router picks per request.** The same JSON prompt went to two different models.
  So for a batch, run `pick` once and pass that model as `--model` for every row.
- **`--json` works through the router.** All three JSON replies parsed. So `--json`
  stays on the router.
- **`--image` works through the router.** The model named the color correctly. So
  `--image` stays on the router.
- **A tiny budget can come back empty.** With 16 tokens the reply was empty with finish
  reason `length`. In this probe the router refused a request that turned reasoning off (HTTP 400). So
  through the router the retry only doubles the budget. At 32 tokens it was still empty,
  and the script failed with a message that names the model and the budget. Give a
  reasoning question a real budget.

Total spend for all nine probe calls was $0.00054.

These results are one day's sample. The router can change which model it picks, so trust
the trailer on each call, not this table.
