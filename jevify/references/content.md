# Content and brand

Outcomes this unlocks: check every draft against every brand rule before an editor sees it,
flag claims your sources do not support, and mine thousands of reviews and comments for the
angles customers actually use.

## Workflow map

| Step | Who | Why |
|---|---|---|
| **Brand rule check** (one question per rule: pass, fail, unclear) | **Jev** | meaning; send failures back to the writer with the failed rules attached |
| **Is each claim supported by its source?** | **Jev** on claim plus source excerpt | meaning; a pre-screen ahead of a human fact-checker, never the final check |
| Split an article into claims; find the source excerpt for each | code or a writing model | Jev selects and judges, it does not extract new text |
| **Review mining** (complaint topic, unhappy or not, switching intent) | **Jev** | meaning at volume |
| Count topics, trend them by month | code | arithmetic over Jev's labels |
| **Comment triage** (spam, question, praise, needs a reply today) | **Jev** | then a writing model drafts replies only for rows Jev marked worth replying |
| **Does this post discuss X?** (pricing, a competitor, a product line) | **Jev** per post | meaning; a keyword search misses "plans start at $49" |
| Share of posts that discuss X | code | count Jev's yes answers; never ask Jev to count |
| **Is this idea already covered?** | **Jev** | compare the reader's question, not the title (`assets/jobs/aeo-duplicate-idea.json`) |
| Write, edit, reply | writing model, then a person | Jev cannot write |

## Ready jobs

- `assets/jobs/content-brand-rules.json`: pass, fail or unclear per brand rule. Replace the three
  sample rules with yours; one question per rule.
- `assets/jobs/content-claim-support.json`: supported, partly, contradicted or not addressed. Input
  needs `claim` and `source_excerpt`.
- `assets/jobs/content-review-mining.json`: complaint topic and switching risk per review. Edit the
  topic list to your product.
- `assets/jobs/content-mentions.json`: does this text discuss the topic (yes, no, unclear). Input
  needs a `text` column; put the topic in `shared_state`.

## Common mistakes

- **Treating a claim check as fact-checking.** It tells you where to look, not what is true.
  A person still clears anything published.
- **One catch-all "is this on brand" question.** It hides which rule failed. One question per
  rule costs almost nothing extra because they run in the same call.
- **Personal data in reviews or comments.** Strip names, emails and handles in code before
  sending.
