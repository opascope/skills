# Plan template with a verification step per action

Use only the sections that help someone execute the task cold.

```markdown
# Plan: task title
Goal:
Scope and non-goals:
Constraints and existing authorization:
Inputs: exact paths, formats and facts already verified
Tools: actual command syntax
Output: intended files and their audience

## Steps
### 1. Action on a specific object
Do:
Depends on:
Verify: command or named review, expected result, evidence location
Status: pending

## Review
Mode: independent review or sequential self-review
Feasibility findings:
Coverage findings:
Scope findings:
Revisions and unresolved decisions:

## Final proof
End-to-end check:
Result: not run yet
```

Code example: add a `--count` option to an existing text utility. First read its
argument handling and current tests. Then implement line counting without
changing existing output modes. Verify empty input yields 0 and a final line
without a newline is counted. Finally run existing tests to detect regressions.
The output is the utility change and those test results, not a new framework.

Non-code example: compare two supplied instruction documents. First inventory
requirements with document and section sources. Then classify agreements,
conflicts and unique requirements. Finally verify every inventoried requirement
appears in exactly one category and that conflicts quote both relevant sources.
No live research or external publishing is implied.
