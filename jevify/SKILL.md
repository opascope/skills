---
name: jevify
description: Find the judgment calls in a marketing workflow (AEO and SEO, paid media, creative, landing pages, content) that Jev can make on every row, then build and run them as Jev jobs over a CSV with a sample run, a blind human check, a confidence cutoff and the cost shown first. Use when asked to jevify a workflow, where Jev fits in marketing work, or to classify, score or label keywords, search terms, pages, ads, reviews or drafts with Jev. Not for writing copy; Jev cannot write.
allowed-tools: Read Glob Grep Bash Write Edit AskUserQuestion
metadata:
  portability:
    claude: full; runs the script with Bash
    codex: full; runs the script with the shell
---

# Turn marketing judgment calls into Jev jobs

Read [shared.md](shared.md) first. Save the job file with kind `job`.

Jev (by TypeSafe) is a decision model. It never writes. You give it text (the state) and
closed questions. It returns a pick, a yes probability or a rubric score, each with a
confidence. In a check on synthetic rows it took about a third of a second per call and
about $0.03 per 1,000 short rows, so a judgment a team makes by hand on a sample can run
on every row.

The script sits beside this file. Run it by its full path:
`python3 "<this skill's directory>/scripts/jev.py" <command> ...`. Resolve the directory
from where you loaded this SKILL.md; do not rely on a variable for it. For API details the
script does not cover, read https://docs.typesafe.ai/llms.txt.

## The rule: Jev reads meaning, code does numbers

Split every workflow before building anything.

| Who | Takes | Examples |
|---|---|---|
| Jev | What a piece of text means | a query's intent, a page's type, whether a page keeps an ad's promise, whether a draft breaks a brand rule |
| Code | Anything computed from numbers or dates | CPA against target, volume floors, counts, percentages, date windows, dedupe, joins |
| A writing model | Any words that come out | headlines, replies; only for rows Jev marked worth it |
| A person | Anything that spends, publishes or sends when Jev is unsure | adding negatives, pausing ads, publishing |

Never ask Jev to count, compute or compare dates; TypeSafe lists these as weak spots
(https://docs.typesafe.ai/model-jaggedness/jev-1.13.md). Never ask it for a rationale.

The split is per row. "What share of these posts mention pricing" is a Jev question on
each post (yes, no, unclear) plus a code count of the yes answers. Use
`assets/jobs/content-mentions.json`.

Asked to write something? Say Jev cannot write. Have a writing model draft, then offer a
Jev job that scores the drafts and keep the top ones.

## Workflow

Copy this checklist and tick it off:

```
Jevify progress:
- [ ] 1. Pick the discipline and read its file (table below)
- [ ] 2. Map the workflow: mark each judgment Jev, code, writer or person
- [ ] 3. Pick one Jev judgment: many rows, options a person could apply the same way
- [ ] 4. Write the job file (copy a ready job from assets/jobs/; fill every REPLACE)
- [ ] 5. validate the job against the input file; fix every error and warning
- [ ] 6. Sample run on 20 rows; read every answer yourself
- [ ] 7. review, a person labels the sheet blind, calibrate, set the cutoff
- [ ] 8. Full run
- [ ] 9. Report the result in marketing terms
```

If a sample answer looks wrong, fix the question (usually the option descriptions) and
repeat steps 5 and 6. Typed output guarantees the format, not the truth.

Save the job file you wrote with `python3 "KIT" save TASK_ID job < JOB.json`. Results land
next to the input CSV in the project, not in `.opascope-work`.

## Disciplines

| Discipline | Read | Ready jobs in assets/jobs/ |
|---|---|---|
| AEO and SEO | [references/aeo-seo.md](references/aeo-seo.md) | `aeo-keyword-intent`, `aeo-page-type`, `aeo-ai-answer-mention`, `aeo-duplicate-idea` |
| Paid media | [references/paid-media.md](references/paid-media.md) | `paid-search-term-intent`, `paid-ad-landing-match` |
| Creative | [references/creative.md](references/creative.md) | `creative-ad-grade` |
| Landing pages | [references/landing-pages.md](references/landing-pages.md) | `paid-ad-landing-match`, `landing-objections` |
| Content and brand | [references/content.md](references/content.md) | `content-claim-support`, `content-brand-rules`, `content-review-mining`, `content-mentions` |

Read only the file the task needs. Before writing questions, read
[references/writing-questions.md](references/writing-questions.md). Before setting a
cutoff, read [references/calibration.md](references/calibration.md).

## Running it

```bash
python3 "SCRIPT" validate JOB --items rows.csv
python3 "SCRIPT" run JOB rows.csv --limit 20          # the sample
python3 "SCRIPT" review rows.JOBNAME.csv --question QID
python3 "SCRIPT" calibrate rows.JOBNAME.review-QID.csv rows.JOBNAME.csv --question QID
python3 "SCRIPT" run JOB rows.csv                     # the full run
```

SCRIPT is the full path to `scripts/jev.py`. JOB is a path, or the name of a ready job in
`assets/jobs/`. `validate` works without `--items`; with it, it also checks the columns.

`run` prints a cost estimate first and refuses above `--max-cost` (default $1). Quote that
estimate and the cost the run reports; never guess a cost. Jev is billed per input token
and output is free (https://docs.typesafe.ai/models.md), so longer rows cost more. In a
check on synthetic rows, 1,000 pages of 3,000 characters cost about $0.05.

Results are written as `rows.JOBNAME.csv`: the original columns plus, per question, the
answer, its confidence and its route (`auto` or `review`). A choice answer is the option
name. A score answer runs from 0 to the number of levels minus 1. A noul answer is the yes
probability.

Keys: `OPENROUTER_API_KEY` (https://openrouter.ai/keys) is the default route, through
OpenRouter to the latest Jev. `TYPESAFE_API_KEY` is used when it is the only key set. With
neither, stop after writing and saving the job file, and tell the user which key to add.

## Reporting

Lead with the marketing result, then the numbers behind it. A made-up example of the shape:

> Sorted N search terms by buyer intent for $X. A look like negatives; B are unsure and
> need a person. Cutoff C, set from a 20-row blind check (K of 20 right).

Always include rows run, the share sent to review, the cost, and how the cutoff was set.
Claim no accuracy beyond the check that was actually run. If no one has labeled the check
yet, say the cutoff is unchecked.

Follow shared.md's Asking and Reporting sections.

## Keep out

- Personal data: names, emails, phone numbers, anyone under 18. Strip them in code first.
  Zero data retention is an enterprise option (https://docs.typesafe.ai/legal.md), not the
  default.
- A model scoring its own fit for a use case is not evidence that it fits.
