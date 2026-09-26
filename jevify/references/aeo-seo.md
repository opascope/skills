# AEO and SEO

Outcomes this unlocks: know which keywords deserve content, sort a whole site by page type
and intent, and see which AI answers name you or a competitor, on every row
instead of a sample.

## Workflow map

| Step | Who | Why |
|---|---|---|
| Pull keywords, volumes, difficulty (Semrush, Ahrefs, GSC) | code | numbers and exports |
| Drop branded, zero-volume, duplicate rows | code | exact filters |
| **Keyword intent** (learn, compare, buy, go somewhere) | **Jev** | meaning of a short query |
| **Content format per keyword** (guide, listicle, comparison, glossary) | **Jev** | meaning |
| Decide write / skip from intent plus volume and difficulty | code | rules over Jev's label and the numbers |
| **Is this idea already covered by a published page?** | **Jev** | compare the reader's question, not the title; two pages can sound alike and answer different questions |
| **Page type and visitor intent across a crawl** | **Jev** | meaning; about $0.05 per 1,000 pages of 3,000 characters in a synthetic check |
| **Refresh triage per URL** (keep, update, merge, remove) | **Jev** on page text + **code** on traffic trend | trend is a number; staleness is meaning |
| **Did the AI answer name us, a competitor, or neither?** | **Jev** | reading an answer |
| **Forum or backlink relevance** (is this thread about our topic, which of our pages fits) | **Jev** | meaning |
| Count mentions, share of voice, trends over time | code | arithmetic over Jev's labels |
| Write the brief or article | writing model | Jev cannot write |

## Ready jobs

- `assets/jobs/aeo-keyword-intent.json`: intent and best content format per keyword. Input needs a
  `keyword` column.
- `assets/jobs/aeo-page-type.json`: page type and visitor intent per URL. Input needs `url`, `title`,
  `text` (trim page text to the main content; a few thousand characters is plenty).
- `assets/jobs/aeo-ai-answer-mention.json`: whether an AI answer recommends, mentions or omits the
  brand, and whether a competitor comes first. Input needs `prompt` and `answer`; put the
  brand and competitor names in `shared_state`.
- `assets/jobs/aeo-duplicate-idea.json`: does a published page already answer this idea's reader
  question. Input is one row per idea and candidate page (`idea`, `existing_title`,
  `existing_summary`); pick the candidate pages in code first (same topic cluster or top
  search matches), since comparing every idea with every page grows fast.

## Fixing the options, not the cutoff

When a sample puts a row in the nearest wrong label with middling confidence, the options
usually lack a place for it. A news query forced into `listicle` needs a `news` option, not
a higher cutoff. Add the missing option, rerun the sample, and read the answers again.

## Common mistakes

- **Deciding whether two keywords share a search result.** That is a fact about result
  pages, not about meaning. Compare actual result URLs in code.
- **Asking whether a page is relevant to a goal.** Goal questions are harder to check.
  Replace them with concrete options ("product page about our category", "unrelated"), or
  budget for more review.
- **Commercial-sounding articles.** A product page and an article can read alike. Describe
  the boundary in the options.
- **Numbers inside the question.** "Is this keyword worth writing for given 90 searches a
  month" mixes a number judgment into a meaning judgment. Ask intent; let code apply volume.
