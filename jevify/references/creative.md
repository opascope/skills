# Creative

Outcomes this unlocks: tag a whole ad library by hook, angle and claim risk in seconds, see
which formats survive in a category, and gate briefs and drafts before they cost a shoot.

## Workflow map

| Step | Who | Why |
|---|---|---|
| Pull ads (Ad Library, platform export) with copy and transcript text | code | Jev reads text only, no images or video; transcribe or describe first |
| **Hook type, angle, offer, CTA, funnel stage** | **Jev** | meaning; one team tagged about 10,000 Meta ads at $0.07 per 100 |
| **Claim risk** (bold or unprovable claims) | **Jev** flags, person clears | pre-screen only |
| **Boldness or pattern-interrupt score** | **Jev**, score | rank by it; do not read it as an absolute |
| Join tags to spend, days live, CPA | code | numbers |
| Which hooks and formats survive longest | code | arithmetic over Jev's tags; one category study found talking-head UGC 29% of ads shown but only 9.5% still live |
| **Brief gate** (does the brief have a clear hook, audience, proof) | **Jev** | score each criterion separately, shoot only the top |
| **Does this hook sound AI-written?** | **Jev** | a common use; treat as a flag for a human editor |
| Write hooks, scripts, briefs | writing model | Jev cannot write; send the failed criteria back to the writer |

## Ready jobs

- `jobs/creative-ad-grade.json`: hook type, claim risk and CTA clarity per ad. Input needs
  `ad_text` (copy plus any transcript).

## What went wrong for others

- **Refusing to let Jev judge creative.** Grading what an ad's text says is Jev's strength;
  the performance side is what belongs in code.
- **Images and video sent as links.** Jev reads text only. It cannot see a thumbnail.
- **One question for "is this good".** Split into separate questions per criterion (hook,
  proof, CTA, voice). A single catch-all question spreads confidence and tells the writer
  nothing about what to fix.
