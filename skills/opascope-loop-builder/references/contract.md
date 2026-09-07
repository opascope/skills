# Loop contract format and proof rules

Create a task through `kit.py new`. In that directory, create:

- `CONTEXT.md`: objective, scope, stable headings, inputs and settled decisions.
- `SAFETY.md`: applicable project constraints, allowed scope and external-action limits.
- `CHECKLIST.md`: item IDs, pending/in_progress/done/blocked status, dependencies
  and each proof. At most one in_progress item; resume it first.
- `NOTES.md`: checkpoint updated after meaningful substeps, plus append-only decisions.
- `BLOCKERS.md`: active blockers as `- BLOCKED: reason; unblock condition`.
  A heading alone is fine when none are active.
- `init.md`: explicit read order: project instructions, CONTEXT, SAFETY, loop.json,
  CHECKLIST, NOTES, BLOCKERS. Resume the checkpoint before picking a new item.
- `loop.json`: executable contract, using the schema below.

Example for a fictional task that creates a greeting file:

```json
{
  "schema": 1,
  "objective": "greeting.txt contains exactly hello followed by a newline",
  "items": [
    {
      "id": "greeting",
      "description": "Write the requested greeting file",
      "depends": [],
      "proof": ["python3", "-c", "from pathlib import Path; assert Path('greeting.txt').read_text() == 'hello\\n'"]
    }
  ],
  "final_proof": ["python3", "-c", "from pathlib import Path; assert Path('greeting.txt').read_text() == 'hello\\n'"],
  "verifier_files": [],
  "outputs": ["greeting.txt"]
}
```

Proofs run from the project root. Exit 0 means pass; all other exits fail.
Arguments are passed directly, with no shell interpolation. Check exact
contents, counts or behavior inside the command. Do not mask missing inputs
with `|| true` or accept an empty checklist as completion. Put any verifier
scripts and test fixtures used by proofs in `verifier_files`; their contents
are fingerprinted when sealed. Inline checks are covered by the loop.json seal.

For a soft criterion, identify an external reviewer and specific rubric. Keep
that item blocked until review occurs. Include the resulting review receipt
and the reviewed output revision in the contract's verifier_files when making
the reviewed replacement task. Never let a worker manufacture its own sign-off.

Seal only after reviewing the contract. Re-sealing unchanged files is
idempotent; changed criteria require a new task. The runner also fingerprints
the seal itself in memory. This catches accidental drift, not a malicious
worker that has unrestricted filesystem access. The user's runtime permissions
remain authoritative. See the package's docs/loops.md for launcher behavior.
