# Running a prepared loop

The loop-builder skill writes the contract described in its
[reference](../skills/opascope-loop-builder/references/contract.md). A loop task
is created by `kit.py new`, so it shares the artifact substrate with the other
skills. All file paths in proof arguments are relative to the project root
unless absolute. Keep verifier scripts within that project and list them in
`verifier_files` before sealing.

From the package directory:

```sh
python3 kit.py loop validate /path/to/task
python3 kit.py loop seal /path/to/task
python3 kit.py loop run /path/to/task --runtime codex --steps 10 --seconds 300
```

Use `--runtime claude` for Claude Code. Both run the same portable contract.
No native goal or special model alias is needed. One run allows at most the
requested number of fresh runtime calls. Each call receives the cold-read
instructions and checkpoints; conversation memory is not required. A later
explicit run grants a new bounded budget and resumes from the files.

The Codex adapter uses `codex exec --skip-git-repo-check --sandbox workspace-write`.
The Claude adapter uses print mode, acceptEdits, and Read/Write/Edit/Bash tools.
It permits shell execution for the authorized task; the runner does not supply
a filesystem sandbox for Claude. Existing host restrictions still apply. Neither
adapter passes a bypass-permissions flag. Do not run untrusted contracts.

Proof argument arrays are executed by Python directly with your privileges,
outside the runtime sandbox. Review them as executable code before running.
Every proof must exit 0 for success, including the final behavioral proof.
The runner stores actual output and exit status in task proof artifacts, and
runtime output in separate artifacts. Logs may contain project information;
review them before sharing. Standard output reports status, not full logs.

| Exit | Meaning |
|---|---|
| 0 | Every item proof and final proof passed. |
| 1 | Invalid contract, changed criteria, missing executable or another setup error. |
| 2 | Active blocker or runtime failure. |
| 3 | Iteration cap, timeout, or three calls without changed proofs/declared output. |
| 130 | User interrupted the run. |

A blocker is an active `- BLOCKED:` line in BLOCKERS.md. Resolve its stated
condition, move its history into NOTES.md, then explicitly rerun. A blocked or
paused loop is not done. No automatic retries after process exit, quota wakeups,
daemon or scheduler are installed. The per-call timeout stops the child's process
group. A hard crash can leave run.lock; inspect for a surviving process before
manually removing that lock. The lock is not removed speculatively by the runner.

Criteria, context and verifier files are sealed before execution and compared
before and after calls and proofs. This catches accidental changes, including
removed requirements. It cannot defend against malicious code with the same
filesystem permissions. Changed criteria require a new reviewed task; the old
task remains intact. If a task needs judgment, obtain an independent review of
the actual output and tie its receipt to that revision. The worker cannot
certify its own judgment result merely by writing PASS.
