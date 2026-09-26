# AEO and SEO

Outcomes this unlocks: know which keywords deserve content, sort a whole site by page type
and intent in a minute, and see which AI answers name you or a competitor, on every row
instead of a sample.

## Workflow map

| Step | Who | Why |
|---|---|---|
| Pull keywords, volumes, difficulty (Semrush, Ahrefs, GSC) | code | numbers and exports |
| Drop branded, zero-volume, duplicate rows | code | exact filters |
| **Keyword intent** (learn, compare, buy, go somewhere) | **Jev** | meaning of a short query; the idea practitioners proposed most, 58 to 71 times independently |
| **Content format per keyword** (guide, listicle, comparison, glossary) | **Jev** | meaning |
| Decide write / skip from intent plus volume and difficulty | code | rules over Jev's label and the numbers |
| **Is this idea already covered by a published page?** | **Jev** | compare the reader's question, not the title; two pages can sound alike and answer different questions |
| **Page type and visitor intent across a crawl** | **Jev** | our tested case: 1,000 pages in 34 to 44 seconds for about $0.10 to $0.13 |
| **Refresh triage per URL** (keep, update, merge, remove) | **Jev** on page text + **code** on traffic trend | trend is a number; staleness is meaning |
| **Did the AI answer name us, a competitor, or neither?** | **Jev** | reading an answer; one team cut AEO evaluation cost 54% this way |
| **Forum or backlink relevance** (is this thread about our topic, which of our pages fits) | **Jev** | meaning |
| Count mentions, share of voice, trends over time | code | arithmetic over Jev's labels |
| Write the brief or article | writing model | Jev cannot write |

## Ready jobs

- `jobs/aeo-keyword-intent.json`: intent and best content format per keyword. Input needs a
  `keyword` column.
- `jobs/aeo-page-type.json`: page type and visitor intent per URL. Input needs `url`, `title`,
  `text` (trim page text to the main content; a few thousand characters is plenty).
- `jobs/aeo-ai-answer-mention.json`: whether an AI answer recommends, mentions or omits the
  brand, and whether a competitor comes first. Input needs `prompt` and `answer`; put the
  brand and competitor names in `shared_state`.
- `jobs/aeo-duplicate-idea.json`: does a published page already answer this idea's reader
  question. Input is one row per idea and candidate page (`idea`, `existing_title`,
  `existing_summary`); pick the candidate pages in code first (same topic cluster or top
  search matches), since comparing every idea with every page grows fast.

## Worked example: fixing the options, not the cutoff

A 20-keyword sample of an agency's own ranking keywords (live, 2026-09-24) put "search
console news september 2025" in `listicle` at 0.81 confidence: the format options had no
place for news, so Jev picked the nearest wrong label. Adding a `news` option and a "a brand
name on its own" example to `go_somewhere` moved those rows to `news` at 1.00 and raised the
share clearing the 0.9 cutoff from 25% to 40% (format) and 45% to 55% (intent). Same model,
same keywords, better options.

## What went wrong for others

- **Keyword clustering by "same search?" looked 95% right and was 45% wrong** against real
  Google results; product variants like "k8" and "k8 pro" got merged. Do not use Jev to
  decide SERP overlap; compare actual result URLs in code.
- **Relevance to a goal is the weakest question type** we measured (63 to 77% agreement vs
  83 to 91% for page type). If you ask "is this page relevant to our strategy", budget for
  more review, or replace it with concrete options ("product page about our category",
  "unrelated").
- **Commercial-sounding articles** were the most common page-type split in our crawl (a
  product page labeled article 90 times in 999). Describe the boundary in the options.
- Keep numbers out of the question. "Is this keyword worth writing for given 90 searches a
  month" mixes a number judgment into a meaning judgment; ask intent, let code apply volume.
