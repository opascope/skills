# Skills package roadmap

The skills package (opascope/skills) is our public set of reusable AI work procedures ("skills"), published as one versioned package. It is at version 0.6.0, released 09-26. There is no open skill work and no open change request, so this roadmap is small on purpose.

This is a plain roadmap. The manage-roadmap engine is not bootstrapped here (no generated status block, no `milestones/` tree); bootstrapping it is a separate docs-migration step, tracked as m01 below.

## Now

- Nothing. Upkeep only.

## Next

- **m01 Docs and roadmap in place.** The skills package gets the same roadmap and documentation layout as the other three projects, so the daily documentation routine can cover it. Done when: a `docs/roadmap/` folder exists and passes the documentation check; the K.07 archive step is landed (old release results out of the living docs, the CHANGELOG link fixed); and the unfinished `docs/maintain` upkeep branch is merged or closed. The daily documentation routine stays off until the one-time migration is finished across the repos (R-2026-10-06-10).

## Later

- **m02 Next release, when a skill change lands.** Each public release has its VERSION, CHANGELOG and release tag in step, under the one opascope-wide release standard with plain-English GitHub Release notes (R-2026-10-06-09, R-2026-10-06-21). This release also carries version 2 of the jevify skill, which uses Jev, a decision model that can only pick from the options it is given (waits on Gus item B-082).

## Inbox

- 2026-10-05 Remove about 12 old code branches whose work is already in past releases (D-041). Max approves the list before anything is deleted.

## Shipped

- 0.6.0 (2026-09-26): six skills for running work, plus skills for OpenRouter (one account that reaches many AI models) and jevify. Documentation moved to the standard layout on 10-02; the K.07 archive and CHANGELOG-link fix landed (#32).
