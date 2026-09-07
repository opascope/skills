# Work-Process Skills for Claude Code and Codex CLI

The decisions around the work, made explicit. One install, both runtimes.

[![Tests](https://img.shields.io/badge/tests-49%20passing-brightgreen)](docs/verification.md)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Runtimes](https://img.shields.io/badge/runtimes-Claude%20Code%20%7C%20Codex%20CLI-blueviolet)](#which-runtimes-are-supported)
[![Dependencies](https://img.shields.io/badge/dependencies-none-lightgrey)](#requirements)

Most agent skill collections add domain capability: write React, audit security, edit video. These add work process. They handle the decisions around the work. That makes it easier to direct, verify, and resume.

Useful agent workflows often have nothing to do with your domain.

## What problem does this solve?

**Summary:** Coding agents fail in predictable ways. They start before the request is clear. They call work done without proving it. They lose the thread between sessions. They run unattended loops that report success without evidence. Each skill here answers one of those failures with a saved file rather than a longer prompt.

| Failure | Skill | What you get |
|---|---|---|
| Agent guessed instead of asking | `interrogate` | A brief labelling every claim SAID, FOUND, or GUESSED |
| "Done" means nothing checkable | `define-done` | One sentence you can check as true or false: "This is solved when ..." |
| Steps without verification | `planning` | Every step has one action and a check that passes or fails |
| Context lost between sessions | `session-handoff` | A handoff with the exact next step and proof of what is already done |
| Process nobody questions | `optimize` | A deletion-first audit that never edits its target |
| Unattended runs that lie | `loop-builder` | A contract that checks its own proofs |

## Install

You need Git, Python 3.9 or newer, and a logged-in Claude Code or Codex CLI. It works on macOS, Linux, and WSL.

Nothing to install with pip, no JavaScript runtime, and no build step.

Run this command:

```sh
git clone https://github.com/opascope/skills.git opascope-skills && python3 opascope-skills/install.py
```

The installer asks which agent you use. It asks whether to install for every project or only this one. It shows you where it will put things before it acts.

If a skill directory of the same name already exists, it stops. It will not write over one.

Leave the cloned folder where it is. The installed links point back to it.

After you install, try one skill. In Claude Code:

```text
/opascope-define-done I keep losing notes. What would solved look like?
```

In Codex CLI:

```text
$opascope-define-done I keep losing notes. What would solved look like?
```

If you are unsure where to start, type `/opascope` or `$opascope`.

## The skills

Skill names start with `opascope-`. To choose, use `opascope`. The same files serve both agents. You do not need a second copy to keep in sync.

| Skill | What it promises |
|---|---|
| `interrogate` | Asks about the choices you did not make, and never about the ones you did. |
| `define-done` | Writes one sentence you can check as true or false. |
| `planning` | Gives every step one action and a check that passes or fails. |
| `session-handoff` | Leaves the next session the exact next step and proof of what is already done. |
| `optimize` | Says what to delete, argues the other side first, and changes nothing. |
| `loop-builder` | Sets up a long unattended run with a real finish line and a spending limit. |

### How do I stop an agent from guessing at requirements?

**`opascope-interrogate`** drafts the plan it is about to execute. It labels every claim SAID (you supplied it), FOUND (verified, with source), or GUESSED (an assumption). A plausible inference still counts as GUESSED. It resolves what the environment can answer before involving you. It asks at most three independent questions per round. Scope-changing ones come first.

Zero questions is a valid result, and it will not invent uncertainty to look thorough.

### How do I define "done" for an AI coding task?

**`opascope-define-done`** produces one sentence you can check as true or false. It describes the observable world after the work, not the implementation. It runs a trap test: could every clause be true while the original problem remains? If so, the missing pain goes into the objective.

- Solution framing, rejected: "Create a searchable folder."
- End-state framing, accepted: "This is solved when a reader can find each note from its topic, and every original note is preserved byte-for-byte."

### How do I make an agent plan with verifiable steps?

**`opascope-planning`** gives each step one action, its dependencies, a check that passes or fails, and a place to find proof. A step without a check is an assumption about success.

Where an independent reviewer is available it uses one. Where none exists it runs three sequential passes: feasibility, coverage, scope. It labels the result "sequential self-review, not independent".

### How do I hand off context between agent sessions?

**`opascope-session-handoff`** cross-checks conversation claims against the current files, command results, and git status before writing anything. A directory listing proves a file exists. It says nothing about whether content changed. It publishes a handoff that cannot be changed, then runs the resume command to prove the pickup works.

A new session picks up with "use opascope to resume my notes task". Nothing is injected into your startup files and no automatic pickup is claimed.

### What can I delete from this codebase or process?

**`opascope-optimize`** audits with removal as the starting point and never changes the audited target. Every part gets one label, with evidence: `KILL`, `COMPRESS`, `KEEP`, or `QUESTION`.

Before any `KILL` it builds the strongest evidence-based case for keeping the part. It shows why that case does or does not survive. The audit treats inactivity as a signal to investigate, not a deadline. Absence of a recorded incident does not show a safety net is unnecessary.

### How do I run Claude Code or Codex unattended without false completions?

**`opascope-loop-builder`** prepares a loop with a limit you set. It saves files as it goes. It uses criteria you can check. Then a small foreground Python process runs it.

- Proofs are argument arrays, never shell strings, executed by the parent process.
- A worker's claim that an item is done proves nothing. The runner checks.
- Criteria and verifier files get a hash at seal time. The runner rechecks them before and after every runtime call and every proof.
- Distinct exits: blocker, iteration cap, timeout, three calls with no measurable progress. None of those means completion.
- Judgment-only completion requires an external review record. A worker cannot certify its own judgment by writing PASS.

```sh
python3 kit.py loop run /path/to/task --runtime claude --steps 10 --seconds 300
python3 kit.py loop run /path/to/task --runtime codex  --steps 10 --seconds 300
```

## Which runtimes are supported?

**Summary:** Claude Code and Codex CLI, from one source. The same skill text runs on both. No second copy is kept in sync.

| | Claude Code | Codex CLI |
|---|---|---|
| Invocation | `/opascope-define-done` | `$opascope-define-done` |
| Discovery path | `.claude/skills` | `.agents/skills` |
| Loop adapter | `claude -p`, acceptEdits, Read/Write/Edit/Bash | `codex exec --sandbox workspace-write` |
| Questions | Interactive question tool | Question tool, else numbered text with a real wait |

Installation creates a real directory per skill containing links back to the source files. Frontmatter tool names describe capabilities; the host's permissions remain authoritative in both runtimes.

## Where your working files go

Files appear on first use in `.opascope-work/<task-id>/` inside your project. You do not need to set anything up.

They are markdown you can read. They hold notes for the next session, plans, and reports.

The project root is the nearest ancestor containing `.opascope-skills.json`, `.opascope-work`, or `.git`. The package keeps no global current-task pointer. Two projects, or two tasks in one project, never overwrite each other. The work directory records its owning project and refuses use from a different one.

To put them somewhere else, create `.opascope-skills.json` in your project root. Add `{"artifact_dir": "notes/agent-work"}`. The package reads no other keys from that file.

## Install choices, updates, and uninstall

Run these from the cloned folder:

```sh
python3 install.py --runtime both --base /path/to/project --yes
python3 install.py status --base /path/to/project
python3 kit.py update --check
python3 kit.py update --base /path/to/project
python3 install.py uninstall --base /path/to/project --yes
```

Leave off `--base` and it uses your home directory.

An update only runs on a clean checkout of the main branch. It only moves forward to a released version. Pass each place you installed to relink it. Or run the installer there again afterwards.

Ordinary use does not check the network for an update. The skills read only version tags already on your disk.

Uninstall removes the links it made. It removes the record of what it installed. It removes any empty folders it created. Anything you changed or added yourself is kept. It lists the files that stayed. Your working files and the cloned folder stay.

Uninstall before you move or delete the cloned folder.

It does not edit your shell profile or your agent config. It installs nothing that runs in the background.

## How does this compare to other agent skill collections?

**Summary:** Most collections aim for breadth, shipping hundreds of domain skills. This one ships process skills and a runtime that checks them.

| | Skill mega-collections | This package |
|---|---|---|
| Size | Hundreds to thousands | Small and process-focused |
| Scope | Domain capability | Work process, domain independent |
| Runtimes | Usually Claude Code only | Claude Code and Codex CLI from one source |
| Dependencies | Varies, often npm or Python packages | None. Markdown and Python standard library |
| Completion checking | The agent reports done | The parent process runs proofs independently |
| Install | Copy files or add a marketplace | Symlinks with a record, reversible uninstall |
| Tests | Usually none | 49 standard-library tests |

Use a mega-collection for domain capability. Use this alongside it for the decisions around the work.

## Requirements

- Git
- Python 3.9 or newer
- Claude Code or Codex CLI, installed and authenticated
- macOS, Linux, or WSL

It needs no Python packages, no JavaScript runtime, no build step, no database, no background service, and no telemetry.

## Limitations

Sealing catches accidental edits to your criteria, and requirements that went missing. It will not stop malicious code that already holds your filesystem permissions.

Proofs run with your privileges, outside the agent sandbox. Read a contract's proof commands before you run it, the same way you would read any script.

You start every run yourself. Nothing wakes on a schedule, retries after a crash, watches your usage allowance, or runs in the background.

A model can still misjudge. During release testing a Codex worker read an escaped display of its own proof as a literal backslash and stopped. The decoded bytes showed it was wrong.

Usage counts only the skills you invoked by name, so it undercounts. A skill the agent chose on its own leaves nothing to count.

When no independent reviewer is available, a skill reviews its own work and the output says so.

## How can I tell the skills do what they say?

Every skill carries a promise file next to it. The promise uses the same words as the table above.

Next to the promise sit the checks that would catch the skill breaking it. At least one check is a trap. It sets up something the skill should refuse, and fails if the skill does it anyway.

One example: the test asks `loop-builder` to set up a long run and then stop. Its check fails if it started the run.

When you add a skill, you add its promise file beside it. You do not edit any test.

`plainlang.py` checks the wording of this README and every skill name, so a contributor knows their words get checked.

Commands:

```sh
python3 promise.py list
python3 plainlang.py
python3 -m unittest discover -s tests -v
```

## Frequently asked questions

**Does this work with Codex CLI, or only Claude Code?**
Both, from the same files. Codex uses `$skill-name` and `.agents/skills`. Claude Code uses `/skill-name` and `.claude/skills`. Install with `--runtime both`.

**Will it modify my project or my agent config?**
No. It creates symlinks in a skills directory and writes task files under `.opascope-work/`. It never edits your `.gitignore`, shell profile, startup files, or agent instructions. It installs no background service.

**How do I uninstall it cleanly?**
`python3 install.py uninstall --base /path/to/base --yes`. The record of what was installed lists every link and directory created. Uninstall removes only what it owns and reports anything it preserved. Uninstall before moving or deleting the checkout.

**Are these skills specific to a domain or industry?**
No. They are deliberately domain independent and contain no client, company, or project specifics.

**Can I use one skill without the others?**
Yes. They share one task directory but each stands alone. The `opascope` router recommends the smallest useful one rather than running the whole suite.

**Does it send my code or transcripts anywhere?**
No. It sends no telemetry and runs no background update process. The usage command is read only and local only. The version check reads locally fetched Git tags offline unless you explicitly request a network update.

**What happens if a loop cannot finish?**
It exits with a distinct status for blocker, iteration cap, timeout, or three calls without measurable progress. It preserves every file. None of those is reported as success. A later explicit run resumes from the files, even on the other runtime.

**How do I know it actually works?**
`python3 -m unittest discover -s tests -v` runs 49 standard-library tests in temporary directories. Live model-backed verification is documented in [docs/verification.md](docs/verification.md), including the 0.1.0 release results on both runtimes.

## Verification

```sh
python3 -m unittest discover -s tests -v
```

Tests cover both runtime discovery layouts, identical linked skill contents, safety when run twice, real-directory and symlink collisions, preservation of user additions, partial-install rollback, uninstall, file resolution, immutable handoffs, transcript parsing, actual proof execution, and safe Git updates.

Opt-in live checks against your own authenticated runtimes are in [docs/verification.md](docs/verification.md). Loop contracts and their limits are in [docs/loops.md](docs/loops.md). File semantics are in [ARCHITECTURE.md](ARCHITECTURE.md).

## Measure your own usage

```sh
python3 kit.py usage
python3 kit.py usage /path/to/transcripts --json
```

Read only, local only. It counts distinct sessions that invoked skills by name and never transmits or prints transcript contents. It deliberately undercounts implicit selection. See [counting rules](docs/usage.md).

No published claim here depends on this metric. Run it on your own transcripts to see whether process skills are useful in your own work.

## About

Built and used by [Opascope](https://opascope.com), a performance marketing agency that runs its own delivery on coding agents. These are the work habits we needed often enough to package, with the domain specifics stripped out.

MIT licensed. Contributions keep the package small and the files readable on their own. They keep the install free of anything you must install first.
