# Confidence and the cutoff

Confidence is a routing signal, not proof. The script marks each answer `auto` (at or
above the cutoff) or `review`. It starts at 0.9. Set the real cutoff with `calibrate`, per
dataset. TypeSafe explains what confidence means at https://docs.typesafe.ai/confidence.md.

- A cutoff from one client or content type does not carry over to another. Measure it on
  your own rows.
- Set the cutoff by consequence. Tagging for a report can take a lower cutoff than adding
  negative keywords. Anything below the cutoff that spends, publishes or sends goes to a
  person.

## The 20-row check

1. `review` builds a sheet of the 14 lowest-confidence and 6 highest-confidence rows,
   shuffled, with Jev's answers hidden. The confident rows are there because an answer that
   is confidently wrong never flags itself.
2. A person fills `human_label` with the option name (for a choice), the level number
   counting from 0 (for a score), or yes or no (for a noul). You never fill it, and the
   person should not look at Jev's answers first.
3. `calibrate` prints accuracy by confidence band and a suggested cutoff. If any confident
   answer was wrong it says STOP: fix the options and rerun the sample before anything
   else.

Build one sheet per question you will act on. Twenty rows is a sanity check, not proof;
say so when reporting. If nobody can label yet, keep the 0.9 default, send everything below
it to review, and report the cutoff as unchecked.
