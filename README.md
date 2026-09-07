# Opascope Skills

Six work-process skills for Claude Code and Codex CLI. Clarify the request,
define done, plan verifiable steps, preserve context, question unnecessary work,
and prepare bounded autonomous loops.

Useful agent workflows often have nothing to do with your domain. These skills
make the decisions around the work explicit, so the work is easier to direct,
verify and resume.

## Install

Requires Git, Python 3.9 or newer, and an installed, authenticated Claude Code
or Codex CLI. macOS, Linux and WSL are supported. There are no Python packages
to install, no JavaScript runtime, and no build step.

```sh
git clone https://github.com/opascope/opascope-skills.git && python3 opascope-skills/install.py
```

Choose Claude Code, Codex, or both, then choose a global or current-project
install. The installer previews the location and creates namespaced links.
Keep the cloned directory in place. Existing skill directories are reported
as collisions and never overwritten. While this repository is private, Git
access is required to clone and update it.

Open a new session in your project and try:

```text
Claude Code: /opascope-define-done I keep losing notes. What would solved look like?
Codex CLI:  $opascope-define-done I keep losing notes. What would solved look like?
```

Use `/opascope` or `$opascope` if you are unsure where to start.

| Skill | What it does |
|---|---|
| interrogate | Turns unverified guesses into a focused question queue and brief. |
| define-done | Writes the falsifiable sentence that means the problem is solved. |
| planning | Gives each step an explicit action and a pass/fail check. |
| session-handoff | Saves verified progress and the exact next action for cold pickup. |
| optimize | Recommends what to delete or compress, without changing the target. |
| loop-builder | Prepares durable context and bounded Claude/Codex runs with real proofs. |

Every skill name has the `opascope-` prefix. The router is simply `opascope`.
The same files serve both runtimes; no second copy needs to be kept in sync.

## Your working files

Artifacts appear on first use in `.opascope-work/<task-id>/` in your project.
No setup is required. Handoffs, plans and reports are readable markdown. A new
session can say "use opascope to resume my notes task" and load its handoff.
Nothing is injected into your startup files.

For a different location, put `{"artifact_dir": "notes/agent-work"}` in a
project-root `.opascope-skills.json`. This is the only artifact setting.
See [architecture](ARCHITECTURE.md) for root resolution and concurrent tasks.

## Install controls, updates and uninstall

Run these from the cloned package directory:

```sh
python3 install.py --runtime both --base /path/to/project --yes
python3 install.py status --base /path/to/project
python3 kit.py update --check
python3 kit.py update --base /path/to/project
python3 install.py uninstall --base /path/to/project --yes
```

Omit `--base` to use your home directory. Updates require a clean checkout on
main and fast-forward to a stable version tag. Pass each installation base to
relink it, or rerun the installer there afterward. Skill preambles only check
locally fetched tags, so ordinary skill use makes no update network request.

Uninstall removes matching installed links, the receipt, and empty directories
the installer created. Changed links and user additions are preserved and
reported. Your task artifacts and source checkout remain yours. Uninstall
before moving or deleting the checkout. No shell profile or runtime config is
edited, and no background service is installed.

## Measure your own usage

```sh
python3 kit.py usage
python3 kit.py usage /path/to/transcripts --json
```

This read-only command counts distinct sessions that invoked skills by name.
It never transmits anything or prints transcript contents. It deliberately
undercounts implicit selection. See [counting rules](docs/usage.md).

## Loops and verification

Ask loop-builder to prepare a small toy task first. Building a loop creates
artifacts; running it is a separate action unless you requested both. The
foreground runner supports both runtimes, checks proofs independently, keeps
checkpoints, and stops on a blocker or budget limit. Read [loop contracts and
permissions](docs/loops.md) before unattended use.

```sh
python3 -m unittest discover -s tests -v
```

Tests use Python's standard library and temporary directories. Live runtime
verification requires your own authenticated CLIs and is documented in
[verification](docs/verification.md).

MIT licensed, copyright Opascope. Contributions should preserve the small scope,
portable artifacts and dependency-free install.
