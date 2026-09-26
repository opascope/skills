# Landing pages and CRO

Outcomes this unlocks: find every ad and page pair where the page breaks the ad's promise,
and check each page against the objections your buyers actually raise.

## Workflow map

| Step | Who | Why |
|---|---|---|
| Pair each ad with its final URL; fetch main page text | code | joins and scraping |
| **Does the page keep the ad's promise?** | **Jev**, score | route to keep, rewrite the ad, or rewrite the page |
| **Which buyer objections does the page answer?** | **Jev**, one question per objection | meaning; list the objections from sales calls or reviews in `shared_state` |
| **Does the top of the page state the offer?** | **Jev** on the first screen of text | send only the first few hundred characters so the rest does not distract |
| **Lead quality from form answers** | **Jev** | score against your buyer description; feed back into bidding in code |
| Conversion rate, bounce, test significance | code | numbers |
| Rewrite the page | writing model, then a person | Jev cannot write |

## Ready jobs

- `assets/jobs/paid-ad-landing-match.json`: promise match score plus the suggested fix. Input needs
  `ad_headline`, `ad_text`, `landing_page_text`.
- `assets/jobs/landing-objections.json`: whether the page answers three common objections. Edit the
  objections to your buyer's; keep one question per objection.

## Common mistakes

- **Whole-page state for a top-of-page question.** Extra text lowers accuracy. Trim the state
  to the part of the page the question is about.
- **"Is this a good landing page."** Too vague to calibrate. Ask about one specific,
  checkable thing per question.
