# Architecture

## State before skills

All skills use `kit.py` to locate artifacts. The default is
`.opascope-work/<task-id>/` within the project, created only when needed. A task ID
combines a readable slug and random suffix. There is no global current-task pointer.
Two projects, or two tasks in one project, do not overwrite each other's state.

Resolution starts from the requested working directory. The nearest ancestor with
`.opascope-skills.json`, `.opascope-work`, or `.git` is the project root. With none,
the working directory becomes the root on first creation. This supports ordinary
folders, nested projects, git worktrees, and multiple projects without a registry.
To establish a non-git root, run `kit.py new` there before working in a subfolder.

One override: `.opascope-skills.json` at the project root, containing only
`{"artifact_dir": "notes/agent-work"}`. Relative paths resolve from that root;
absolute paths are accepted. An artifact root records its owning project and
refuses use from a different project. Do not share one absolute root between
projects. Changing the setting does not move existing artifacts. Move them yourself
or switch the setting back. No environment-variable configuration is used.

Task artifacts are markdown, with small JSON files for identity and executable
loop contracts. `new` creates only a task identity file. Skills add the artifacts
they need. `save` publishes an immutable timestamped markdown artifact from stdin;
`resume` lists tasks and their latest handoff, or prints a selected task's handoff.
The next session explicitly invokes the router or handoff skill to resume. We do
not install startup hooks or change a project's agent instructions.

Immutable publications use exclusive creation. Mutable loop checkpoints have one
foreground writer, guarded by an exclusive lock. No database, daemon, telemetry,
or background update process is involved. Artifacts may be committed deliberately;
the installer never edits `.gitignore`. People should review task content before
sharing it, as they would any other working notes.

## One source, two discovery locations

Canonical skills live under `skills/opascope-*`. Each installation creates a real
directory with links to its canonical skill's files, including shared `kit.py` and
`shared.md` links. This keeps discovery flat without copying doctrine. Claude Code
uses `.claude/skills`; Codex uses `.agents/skills`, either below the user's home or
a selected project. `AGENTS.md` governs this repository; `CLAUDE.md` links to it.
Neither is injected into a consumer's project.

The receipt in the installation base records every created link and directory.
Existing destinations are collisions unless the receipt owns them and their link
targets still match. Preflight checks the whole installation before changing it.
Uninstall removes only matching recorded links and empty created directories. User
additions and changed links survive and are reported. Project artifacts and the
source checkout are user data and survive uninstall. Run uninstall before moving
the checkout, then reinstall from its new location.

## Runtime behavior

The same skill text runs on both runtimes. Frontmatter describes tools and the
runtime paths; it is not a cross-runtime security boundary. Claude's tool grants
do not restrict Codex. Instructions enforce the workflow's scope and the host's
permissions remain authoritative.

Questions use the host's interactive question tool when available, otherwise
numbered text with lettered choices and a real wait. Plan review and optimization
use separate sequential evidence and counterargument passes when independent
reviewers are unavailable. They label that review honestly. No review is silently
skipped, and an existing user authorization is not replaced by a new approval gate.

Loop building produces a portable contract plus a foreground launcher. `loop run`
starts bounded fresh `claude -p` or `codex exec` calls. Every call reads the same
durable context, constraints, checklist, checkpoint and blockers. The Python parent
runs task proof commands independently and detects changed criteria. Runtime errors,
timeouts, blockers, unchanged progress and the iteration cap produce distinct
non-success exits. A later explicit run resumes from files, even on the other host.
No native goal, model alias, or effort command is assumed.

Proofs are argument arrays, never shell strings. The runner executes them with the
user's privileges, outside the agent sandbox, so only reviewed, trusted proofs
belong in a loop contract. Criteria and verifier files are fingerprinted at seal
time and checked before and after each call and proof. This detects accidental
criterion drift; it is not a sandbox against a malicious agent with filesystem
access. Judgment-only completion requires an external review receipt; it cannot
be certified by the worker itself. See `docs/loops.md` for the contract and limits.

## Updates and measurement

The skill preamble performs an offline version check against locally fetched
release tags. `update --check` explicitly queries the existing Git remote;
`update` fast-forwards a clean checkout to the newest stable version tag and reruns
the installer for its existing receipts. Dirty, divergent and moved installs fail
with a recoverable explanation. Version lookup never transmits project artifacts.

`usage` reads local transcript JSONL and counts distinct sessions with explicit
by-name invocations. It never sends data or prints transcript content. Supported
record types and intentional undercounting are documented in `docs/usage.md`.
