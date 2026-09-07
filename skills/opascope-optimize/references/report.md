# Optimization report format

State the target, inspected scope, unavailable evidence and review mode.

| Component | Purpose | Consumers/writers | Observed use | Cost | Evidence |
|---|---|---|---|---|---|

For each component, record one verdict:

- KILL: exact deletion; what breaks; strongest argument for keeping; why rejected;
  recovery path and uncertainty.
- COMPRESS: overlap evidence; surviving behavior; proposed merge; net reduction.
- KEEP: the dependency or consequence that makes it load-bearing.
- QUESTION: the decision or evidence needed, plus concrete alternatives.

End with prospective impact if the recommendations are applied. Distinguish
measured facts from estimates and unknowns. No edits have been executed.

Example: two files contain identical formatting rules, and the consumer imports
only one. A search finds no consumer for the other. The deletion hypothesis is
reasonable, but an external consumer may be invisible in this checkout. If the
inspection scope cannot exclude it, report that uncertainty rather than claiming
global disuse. A wrapper that enforces access checks is not redundant just
because it delegates its final operation.
