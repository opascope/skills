---
id: cutting-release
title: "Cutting and publishing a release of these skills"
owner: maintainers
covers: ["VERSION", "CHANGELOG.md", "scripts/changelog_guard.py", ".github/workflows/changelog-check.yml", ".github/workflows/release-published.yml"]
---
# Cutting and publishing a release of these skills

This repo follows the Opascope release standard, the same one gustav, uplink and
magic use: a root `VERSION`, a root `CHANGELOG.md` in Keep a Changelog format,
one plain line per change, and a plain-English GitHub Release for every version.

## The per-change rule (enforced in CI)

Every PR adds ONE plain "what users get" line under `## [Unreleased]` in
`CHANGELOG.md`. A change that shows a user nothing (a pure refactor, a test-only
change) puts `no user-visible change` (or `changelog: none`) in the PR body or a
commit message instead. `scripts/changelog_guard.py` is the checker and
`.github/workflows/changelog-check.yml` runs it on every PR.

## How release notes read

Max's bar (2026-10-07): release notes are "plain english, human readable, help a
user understand what the impact is to their day to day, why a version shipped,
whats new". They are the PR-as-decision-record approach
(`docs/guides/agentic-engineering.md`, "Write the PR as a decision record")
turned toward the people who use the tool rather than the reviewer: lead with
what changed for them, say why, then stop.

- **Open with a short "In short" paragraph.** Two to four sentences at the top
  of the version section saying why this version shipped and what a teammate
  will notice in their day. Write it in the cut PR, right after `cut --confirm`
  moves the lines into the dated section. `publish` sends the whole section, so
  the paragraph becomes the top of the GitHub Release.
- **Each line answers "what is different for me?"** Start from the person's
  side: what they can now do, what stopped going wrong, what they no longer have
  to do. Add one clause on why when it isn't obvious.
- **Leave the machinery out of the body.** That means no script or file names,
  flags, function names, check names, config keys or PR numbers. Name a
  command only when the reader types it themselves (`/update-gus`). The diff
  and the PR hold the detail for engineers.
- **One or two sentences per line.** If a line needs a paragraph, it's two
  changes, or the detail belongs in the PR.

Too engineer-facing (from v0.2.0):

> update-gus judges its post-update doctor check by check: a regression is a
> check that passed before the update and fails after it. A check that is new in
> the update, or that reads shared remote state (`docs.heartbeat`, marked
> `remote` on its doctor row), is reported as "new: failing" and never blocks or
> rolls back the update.

Reads right:

> Updating Gus no longer gets stuck on a health check that is new in the update.
> Only a check that worked before and breaks after it stops the update.

A version section then looks like this:

```markdown
## [0.3.0] - 2026-10-08

In short: Gus now updates itself when you open Claude, so you stop running
/update-gus by hand. Slack search moved fully into Uplink, so every search sees
the same history.

### Added
- Gus updates arrive at session start without you waiting. If an update would
  touch files you changed, it leaves them alone and asks you to run /update-gus.
```

## When a release is cut

This repo has one trunk, `main`, and no promote, so a release is cut with every
ship: whenever teammates should update (`kit.py update`). A human can still call
an interim release. Cut only when BOTH gates are green at the head being
released:

1. **Required CI is green at the head.**
2. **There is an independent review at that head.**

If either gate is red, stop. Do not retry, and never fix forward on `main` to
make a release go.

## Cutting a release

`release.py` lives in gustav and runs against this repo with `--repo`. It is not
copied here: `publish` writes a fail-closed `github.release` row to gustav's
outbound ledger before it creates the tag and the GitHub Release. `$GUSTAV` is a
gustav checkout.

```bash
# 1. in a worktree from origin/main
python3 "$GUSTAV/scripts/release.py" --repo . plan           # the next version and its notes
python3 "$GUSTAV/scripts/release.py" --repo . cut --confirm  # bump VERSION, date the [Unreleased] lines
# write the "In short" paragraph, open a PR to main, review, merge it
# 2. from a clean detached checkout of the merged commit
python3 "$GUSTAV/scripts/release.py" --repo . publish --confirm --target <sha>
```

`publish` refuses unless the tree is clean, HEAD is the `--target` commit, and
that commit is on `origin/main`. A re-run at the same commit resumes rather than
publishing twice. The `Release published` check on `main` goes red when
`v<VERSION>` still has no tag two hours after that VERSION reached `main`; it
never publishes anything itself.
