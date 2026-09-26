# Endpoints

What each verb sends and which reply fields it reads. Model names live in
`scripts/roles.json`; this file has none.

## chat and research: `POST /api/v1/chat/completions`

The script sends `model`, `messages`, and `usage: {"include": true}`. `--json` adds
`response_format: {"type": "json_object"}`. `--image` sends the file as a data URL.
`--effort` sets `reasoning`. `--max-tokens` caps the reply.

It reads:

- `model`: the model that answered. With the router or a latest alias this can differ
  from what you sent. The trailer shows this one.
- `choices[0].finish_reason`: `stop` is a clean finish. `length` means the budget ran
  out. `length` with empty content is the thinking-overrun case.
- `usage.cost`, or `usage.cost_details.upstream_inference_cost` when `usage.is_byok`
  is true. If neither is there, the cost is `unknown`.
- `citations`, and `url_citation` notes on the message, for research. They are printed
  as numbered sources.
- `id`: the generation id, starting `gen-`. It goes in the receipt.

## embed: `POST /api/v1/embeddings`

Sends `model` and `input`. Prints the vector size, or the vector with `--json`.

## decide: `POST /api/alpha/decisions`

Sends `model`, `state` and `questions`. Each question is closed: a `choice` from the
`criteria` you supply, with `instructions`. For example:

```json
{"route": {"type": "choice", "instructions": "Which team should take this ticket?",
           "criteria": {"billing": "money, invoices, refunds", "tech": "bugs and errors"}}}
```

The answer comes back per question with a confidence. It can only be one of your
options. Check the confidence before acting on it.

## models: `GET /api/v1/models` and `GET /api/v1/embeddings/models`

Public, no key. Each row has `id`, `context_length`, `pricing` (per token),
`created` and sometimes `expiration_date`.

## key: `GET /api/v1/key`

Credit limit, what is left and what was used on the current key.

## Errors

OpenRouter lists its error codes at https://openrouter.ai/docs/api-reference/errors.

- 401: the key was refused.
- 402: no credit left.
- 429: rate limited; wait and retry.
- 404 about data policy: the account's privacy setting blocks that model.

The script never prints the key or the request headers, even in an error.
