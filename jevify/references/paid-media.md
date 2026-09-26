# Paid media

Outcomes this unlocks: find wasted spend in the full search terms report, catch ads whose
landing page breaks the promise, and grade every ad in an account, not the ten you had
time to open.

## Workflow map

| Step | Who | Why |
|---|---|---|
| Export search terms, spend, conversions, CPA | code | platform data |
| Drop terms already negated, zero-impression noise | code | exact filters |
| **Search term buyer intent** (buyer, researcher, job seeker, wrong product) | **Jev** | meaning of a query |
| Choose negatives: Jev says not a buyer AND spend over $X AND zero conversions | code | rule over label and numbers |
| Add negatives below the cutoff | person | a wrong negative silently kills traffic |
| **Ad to landing page match** (does the page keep the ad's promise) | **Jev** | meaning; does the page say what the ad promised |
| **Ad policy risk before launch** (restricted claims, platform rules) | **Jev** flags, person clears | Jev is a pre-screen, never the compliance sign-off |
| **Creative grading** (hook type, claim risk, CTA, funnel stage) | **Jev** | see [creative.md](creative.md) |
| Stop / scale / hold from CPA, frequency, CTR trend | code | these are numbers; do not hand them to Jev |
| Combine creative grade with performance to pick ads to refresh | code | rule |
| **Lead quality from form answers** (fits our buyer or not) | **Jev** | meaning; feed the score back as a conversion value in code |
| Move bids or budgets | code, gated on confidence | when Jev is unsure, money does not move |
| Write new ad copy | writing model | Jev cannot write |

## Ready jobs

- `assets/jobs/paid-search-term-intent.json`: buyer intent per search term. Input needs a
  `search_term` column; describe what you sell in `shared_state.offer`, since "buyer" only
  means something relative to the offer.
- `assets/jobs/paid-ad-landing-match.json`: how well the page keeps the ad's promise, and the fix.
  Input needs `ad_headline`, `ad_text`, `landing_page_text`.

## Common mistakes

- **Performance decisions handed to a model.** Stop/scale/hold from spend and CPA is
  arithmetic against a target. Compute it; use Jev only for what the ad says.
- **Acting on low confidence with money.** Gate spend changes on confidence and send the
  rest to a person.
- **"Buyer" without an offer.** The same query is a buyer for one account and noise for
  another. Always put the offer in the state.
