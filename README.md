# opascope-skills

This package holds work-process skills for Claude Code and Codex CLI. They do not belong to one job. They help with the decisions around work. They cover what is being asked, what done means, and what the steps are. They cover what got finished, what should not exist, and how to leave a long job running safely. The highest-leverage skills often have little to do with your actual job. They make the decisions explicit, so the work is easier to direct, check, and pick back up.

## Install

You need Git, Python 3.9 or newer, and a logged-in Claude Code or Codex CLI. It works on macOS, Linux, and WSL.

You do not need pip. You do not need a JavaScript runtime. You do not need to build anything.

Run this command:

```
git clone https://github.com/opascope/opascope-skills.git && python3 opascope-skills/install.py
```

The installer asks which agent you use. It asks whether to install for every project or only this one. It shows you where it will put things before it acts.

If a skill directory of the same name already exists, it stops. It will not write over one.

Leave the cloned folder where it is. The installed links point back to it.

The repository stays private for now. You need Git access to clone or update it.

After you install, try one skill. In Claude Code:

```
/opascope-define-done I keep losing notes. What would solved look like?
```

In Codex CLI:

```
$opascope-define-done I keep losing notes. What would solved look like?
```

If you are unsure where to start, type `/opascope` or `$opascope`.

## The skills

Skill names start with `opascope-`. To choose, use `opascope`. The same files serve both agents, so you do not need a second copy to keep in sync.

| Skill | What it promises |
|---|---|
| `interrogate` | Asks about the choices you did not make, and never about the ones you did. |
| `define-done` | Writes one sentence you can check as true or false. |
| `planning` | Gives every step one action and a check that passes or fails. |
| `session-handoff` | Leaves the next session the exact next step and proof of what is already done. |
| `optimize` | Says what to delete, argues the other side first, and changes nothing. |
| `loop-builder` | Sets up a long unattended run with a real finish line and a spending limit. |

## Where your working files go

Files appear on first use in `.opascope-work/<task-id>/` inside your project. You do not need to set anything up.

They are markdown you can read. They hold notes for the next session, plans, and reports.

A later session can say "use opascope to resume my notes task". It will read the notes from the last session.

Your startup and config files stay unchanged.

To put them somewhere else, create `.opascope-skills.json` in your project root. Add `{"artifact_dir": "notes/agent-work"}`. The package reads no other keys from that file.

See `ARCHITECTURE.md` for how the project root is found. It also explains how two tasks at once are kept apart.

## Install choices, updates, and uninstall

Run these from the cloned folder:

```
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

## Measure your own usage

```
python3 kit.py usage
python3 kit.py usage /path/to/transcripts --json
```

This is read-only. It sends nothing anywhere. It does not print what your transcripts say.

It counts how many separate sessions called a skill by typing its name.

It undercounts on purpose. When an agent picks a skill on its own, without you typing the name, that call is not counted.

See `docs/usage.md` for more.

## Long unattended runs

Ask `loop-builder` for a small throwaway task first.

Setting up a run and starting it are two separate things. It will not start one unless you asked for that too.

The runner works with both agents. It checks the results itself instead of taking the agent's word. It saves progress as it goes. It stops on a blocker or when it reaches the limit you set.

Read `docs/loops.md` before you leave one running unattended.

## Checking that the skills do what they say

Every skill states one promise in a file next to it. The words match the table above.

The checks beside the promise would catch it breaking that promise. At least one is a trap. The test sets up something the skill is supposed to refuse. It fails if the skill did it anyway. One example: `loop-builder` is asked to set up a run and stop. The check fails if the skill starts the run anyway.

Adding a skill means adding its promise file next to it. You do not need to edit any test.

Run these commands:

```
python3 promise.py list
python3 -m unittest discover -s tests -v
python3 plainlang.py
```

The tests use only what comes with Python. They write to temporary folders.

Checking against the real agents needs your own logged-in CLI. See `docs/verification.md` for that.

`plainlang.py` is the gate on this file and on every skill name. It checks your wording. A contributor should know their wording will be checked.

## License and credit

MIT licensed, copyright Opascope.

Contributions should keep the package small. Keep the files readable on their own. Keep the install free of anything you have to install first.
