# Migration engine interface (FROZEN)

This file is the freeze point named in ENGINE-CONTRACT-2026-10-01 C-b. The docs plugin
(`docs.py` / `docslib/`) and the roadmap plugin (`roadmap.py`) are built against it, and
`engine/tests/conformance_plugin.py` is the fixture plugin both real plugins' tests run against.
It changes only by contract message between the two skill owners. Python 3 standard library only;
the engine is vendored into each repo at `tools/docs/engine/`.

## 1. Entry point

```
python3 tools/docs/engine/migrate.py <step> --plugin <file.py> [--plugin <file.py> ...]
        [--repo <path>] [--run <YYYY-MM-DD>] [step options]
```

`docs.py migrate <step>` is the single user entry (contract RE10) and calls this with BOTH plugins,
roadmap first. Plugins scan in the order given (RE4: roadmap claims first, docs returns the
complement).

Steps, in order: `inventory`, `triage`, `classify`, `consumers`, `relink`, `approve`, `rehearse`,
`apply`, `verify`. Plus `resume`, `rollback`, `status`, `unlock`, and the post-merge steps
`publish` (Beads) and `close` (issues).

Exit codes: 0 ok; 2 usage; 10 InventoryError; 11 CoverageError; 12 ReviewUnavailable;
13 ReviewRejected; 14 BeadsCollision; 15 ChainBroken; 16 ExternalActionFailed; 17 LockHeld;
18 PreflightFailed. Every error prints its class name and a message; there is no catch-all.

## 2. Data types

```
Item = {item_id, plugin, source_path, anchor, kind, content_hash, span,
        signals: {terminal: bool, last_commit_date: str|None, live_refs: [str]}}
```

- `item_id` = `<plugin>:<source_path>#<anchor>`. Anchor: empty for a whole file; a heading slug for a
  section; `L<start>-<end>` for a line range; `issue/<number>` for an issue; `bd/<id>` for a Beads item.
- `source_path`: a repo-relative tracked path, or `@issues` (open GitHub issue snapshot) or `@beads`
  (Beads export). `@` sources are not files and are outside the path partition.
- `kind` (closed vocabulary): `file` (whole tracked file), `section` (part of a file, `span` set),
  `code` (a tracked file that is not documentation; implicit keep, see section 5), `issue`, `bead`.
- `span`: `[start, end]` 1-based inclusive line numbers for `section` items, else null. It lets the
  engine assert the RE4 section rule (no overlap, every non-blank line covered).
- `content_hash`: sha256 hex of the item's exact text (file bytes, section lines joined with `\n`,
  issue canonical text, Beads row canonical JSON).

```
Disposition = {apply: callable|None, verify: callable|None, external: bool}
Failure     = {item_id: str|None, message: str, blocking: "warn"|"fail"}
Consumer    = {path, line, text, target, role}   role in reader|writer|link|inert
```

Plugins normally use the engine's standard dispositions (`migrate.STANDARD`); there `apply` and
`verify` are None because the engine applies and proves the contract dispositions itself.
`external: True` marks dispositions whose effect happens after merge (issue closures).

## 3. Plugin protocol

A plugin is a Python module file loaded by path. It exposes:

- `name: str` (also the item_id prefix).
- `scan(repo) -> Iterable[Item]`. `repo` is a `migrate.Repo` (section 7). A plugin returns items only
  for paths it owns; it reads `repo.claimed` (path -> plugin name, filled by earlier plugins) to return
  the complement. It emits one item for each `@issues` issue and `@beads` row it owns.
- `dispositions: dict[str, Disposition]`. Keys are disposition names; `record` covers every
  `record:<kind>`.
- `triage(item, cfg) -> str | None`. A disposition (`record:<kind>` allowed) or None (the agent
  classifies it). Contract defaults: terminal work items -> `record:<kind>`; stale non-records ->
  `delete`.
- `consumers(repo, paths) -> list[Consumer]` (optional). Refines or adds consumer findings for the
  moving paths; the engine's single-pass matcher runs first and a plugin finding for the same
  (path, line, target) replaces the engine's.
- `verify(repo, ledger) -> list[Failure]`. Plugin-specific checks after apply. `fail` blocks.

Roadmap only: `approval_lines(repo, diff_summary) -> list[str]` (plain English, product level, human
labels, no ids). Not called by the engine.

## 4. Inventory rules (RE1, RE3, RE4, A1)

- Source universe: `git ls-files` minus `docs.json` `inventory_exclude` globs, plus the open-issue
  snapshot and the Beads export.
- Globs use one gitignore-style matcher (`migrate.glob_match`), anchored at the repo root: `**` =
  zero or more directories, `*` and `?` never cross `/`, a pattern ending in `/` matches everything
  under that directory (so `vendor/` is the root `vendor/` only; `**/vendor/` is any depth).
- Partition: every tracked non-excluded path is claimed by exactly one plugin. A path claimed by two
  plugins, or by none, is an InventoryError listing every overlap and gap. Section items of one file
  must not overlap and must cover every non-blank line; a whole-file item and a section item for the
  same file overlap.
- Duplicate heading slugs in one file take `-2`, `-3` in document order (`migrate.split_sections`).
  A repeated item_id is an InventoryError.
- Issues snapshot (`issues-snapshot.json`, committed in the run dir): OPEN issues only, the issue
  titled exactly `manage-docs heartbeat` excluded. Per issue: number, updated_at, title,
  body {text, sha256}, comments [{author, created_at, text, sha256}].
- Beads: located with `bd where --json` (never a `.beads/` directory check); every bd call pins
  `--db <path>`. `beads-before.jsonl` (committed) is `bd export` of the live DB. No Beads: empty file.
- Public repos (`visibility: public`) get no engine migration: inventory raises PreflightFailed.

## 5. Ledger rows and proofs (contract section 3, Q2)

`ledger.jsonl` row:

```
{item_id, plugin, disposition, source_path, anchor, source_hash, dest: [paths],
 fragments: [{dest, anchor}], omitted: [{line_range, text_sha256, reason}], reason,
 last_commit, receipt, bd_id, journal_ref}
```

Fields each disposition requires; fields not listed for a disposition must be null or empty
(dest, fragments and omitted may be empty lists). Validation is exact.

| disposition | required | proof (after relink replay where noted) |
|---|---|---|
| keep | source_hash | whole file: body (after frontmatter) unchanged after relink replay; section: replayed text present in the file |
| move | dest | every non-blank source line (after replay) present in a dest file, or in `omitted` |
| task | bd_id | id in the candidate `.beads/issues.jsonl`; every non-blank source line in its title/description/design/notes/acceptance text, or `omitted` |
| rewrite | receipt {at, doc_hash, covers_hash, by} | `doc_hash` == sha256 of the current body; covers_hash is checked by the docs plugin |
| record:<kind> | dest (one path under `work/`) | dest bytes identical to the source (file bytes or section text) |
| delete | last_commit | whole file: absent from the tree; section: no proof beyond the ledger row |
| issue:close-tracked | bd_id, journal_ref | id in the candidate export; every non-blank body/comment line in the Beads text or `omitted`; comments by `BOT_AUTHORS` excluded |
| issue:close-rejected | reason, journal_ref | reason non-empty; closure happens post-merge |

`omitted` entries: `line_range` = `"<a>-<b>"`, 1-based inclusive, relative to the item's text;
`text_sha256` = sha256 of those lines joined with `\n`; the engine recomputes it; `reason` non-empty.

Beads items (`kind: bead`) use `keep` (row unchanged in the candidate export apart from
`updated_at`) or `task`-style updates expressed as a classify row with `bd`. Issue items use the two
`issue:` dispositions or `keep` (stays open).

`code` items are not ledger rows: they are an implicit keep proven in aggregate (every `code` path
byte-identical to the base). Readers and writers change in the port PR, never in the migration PR.

Relink replay (RE9): before a keep or move comparison, the engine rewrites old-path targets in the
SOURCE text from the ledger's move map (markdown link targets and bare repo-relative paths, with any
`#anchor` mapped through `fragments`), then compares. Any other difference fails. Links inside
records are exempt.

## 6. Classify output file format

The agent writes a JSONL file with one object per item whose triage result is None, and may
override a triaged item by including it with a non-empty `reason`:

```
{"item_id": "...", "disposition": "move", "dest": ["docs/roadmap/x.md"], "fragments": [],
 "omitted": [], "reason": "...", "receipt": null, "bd_id": null,
 "bd": {"id": "<prefix>-<id>", "title": "...", "type": "task", "priority": 2, "parent": null,
        "text": "<full text carried into the Beads description>"}}
```

`bd` (a new Beads item with a pre-assigned id) or `bd_id` (an existing id) is required for `task`
and `issue:close-tracked`. Pre-assigned ids are carried through apply and publish unchanged.
`migrate classify --from <file>` validates that every untriaged item has exactly one row with a
disposition its owning plugin declares, then writes the draft ledger (`plan.jsonl`).
`migrate classify --worklist` writes the list of items that still need a row.

## 7. Repo object passed to plugins

`repo.root` (Path), `repo.cfg` (docs.json dict), `repo.tracked` (non-excluded tracked paths),
`repo.excluded` ({glob: count}), `repo.claimed` ({path: plugin}), `repo.issues` (snapshot list),
`repo.beads` (export rows), `repo.read_text(path)`, `repo.last_commit_date(path)`, `repo.base`
(base ref). Helpers: `migrate.sha256_text`, `migrate.split_sections`, `migrate.slugify`,
`migrate.body_of`, `migrate.glob_match`, `migrate.make_item`.

## 8. State chain, lock, resume, rollback (manage-roadmap v2 "Engine semantics")

- Run dir `work/runs/<date>-migration/` holds the committed record: `state.jsonl`, `ledger.jsonl`,
  `issues-snapshot.json`, `beads-before.jsonl`, `SUMMARY.md`, `LEDGER.md`, `HANDOFF.md`. Step
  intermediates live in `.docs-cache/migration/<date>/` (gitignored).
- State row: `{step, started_at, inputs_hash, outputs_hash, prev_hash}`. `inputs_hash` = sha256 of the
  canonical JSON of `{"inputs": sorted [[path or item_id, sha256], ...], "prev_outputs": <previous
  row's outputs_hash or "">}`. `outputs_hash` = sha256 of the canonical JSON of the sorted
  `[[file, sha256], ...]` of the step's output files. `prev_hash` = sha256 of the previous row's
  canonical JSON (first row: 64 zeros). Canonical JSON = sorted keys, no spaces, UTF-8.
- Each step re-checks the chain first: a broken `prev_hash` or an output file that no longer matches
  its recorded hash is ChainBroken. Re-running an earlier step drops the later rows (allowed only
  before apply). `resume` prints the next step after the last row that still recomputes.
- LOCK at `$(git rev-parse --git-common-dir)/manage-docs/migration.lock`, created O_EXCL by
  inventory with `{pid, host, started_at, run_dir}`. Later steps of the SAME run dir proceed; a lock for
  another run dir, a concurrent invocation (flock busy), or an open `docs/port-*` / `docs/migrate-*`
  PR at inventory raises LockHeld with the exact clear command (`migrate.py unlock --lock <path>`).
  Never auto-stolen. Removed when local verify passes or rollback completes.
- Rollback: files by `git revert` of the migration commits (or a restore before commit); Beads:
  nothing live before publish; after publish only run-touched ids are undone from `beads-before.jsonl`
  (created ids deleted, updated ids re-imported with `--allow-stale`), refusing with a conflict list
  (BeadsCollision) for any id changed since publish; closures reopened from the journal.

## 9. Approve (L3)

- `approve --review`: every plan row goes to the reviewer (a different model family from the
  classifier: `docs.json` `reviewer_cmd[<runtime>]`), chunked, as quoted data. The reviewer must
  answer this JSON and nothing else:

```
{"chunk": "<id>", "decisions": [{"item_id": "...", "verdict": "approve", "reason": "..."}]}
```

  `verdict` is `approve` or `reject`. Every item_id in the chunk exactly once, validated by code.
  Malformed, empty or a refusal: retry once, then ReviewUnavailable. Any reject: ReviewRejected; the
  rejected items return to classify.
- `approve --confirm <map_hash> --chat-ref "<where Max confirmed>"`: records Max's chat approval of the
  map summary, appends a dated entry (`## YYYY-MM-DD migration map approved`) with the map hash to
  `docs/roadmap/DECISIONS.md`, and writes the approve state row. Refused when `MANAGE_DOCS_ROUTINE=1`
  (a routine run never approves a map) and when the review did not approve every row of that map.

## 10. Rehearse, apply, verify, post-merge

- `rehearse`: throwaway detached git worktree, Beads on a COPY, every GitHub action simulated; writes
  the SUMMARY.md "Rehearsal" section (plan hash and worktree verify output). `apply` refuses without
  a Rehearsal section for the current plan hash.
- `apply` (RE5): applies the plan to the working tree; Beads changes go ONLY to the committed
  `.beads/issues.jsonl` (pre-assigned ids); the live DB is untouched. Refuses while any WRITER still
  mentions an old path (RE7; InventoryError naming each line).
- `verify`: local, or `--ci --base <ref>`. Chain intact; inventory re-run on the merge-base and every
  item has a ledger row (unmapped report empty); every issue and Beads snapshot id conserved;
  excluded and `code` paths byte-unchanged; every row proven; relative links in live docs resolve;
  plugin `verify` failures with `blocking: fail` fail. Local verify writes HANDOFF.md before merge.
- `publish` (after the migration PR merged and CI is green on the merge): journaled import of the
  run-touched rows of the merged export into the live DB by pre-assigned id, idempotent,
  conflict-checked against `beads-before.jsonl` (BeadsCollision).
- `close`: journaled closures, each checked against the merged export and the live DB, idempotent by
  item_id, live issue drift re-checked. In a repo that has `scripts/outbound_ledger.py` (gustav) each
  closure first calls `record_outbound(surface="github.issue_comment", fail_closed=True)`.
- Journals: `~/.config/manage-docs/<repo>/publish.jsonl` and `closures.jsonl` (untracked).

## 11. Test seams (environment)

Fakes for these seams live in `engine/tests/fakes/` (gh, bd, reviewer) and are shared by the
engine suite and both plugins' suites.


`MANAGE_DOCS_GH`, `MANAGE_DOCS_BD` (binaries), `MANAGE_DOCS_REVIEWER` (reviewer command),
`MANAGE_DOCS_CONFIG_HOME` (journal root), `MANAGE_DOCS_RUNTIME` (`claude`|`codex`, selects the
reviewer), `MANAGE_DOCS_ROUTINE` (`1` in routine runs).
