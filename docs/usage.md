# Local usage measurement

`python3 kit.py usage` scans JSONL below the standard Claude Code project
transcript directory and Codex session directory in the user's home. Supply
one or more files or folders explicitly for alternate locations. Missing paths,
unreadable files and malformed records are counted and reported. No files are
created, no network call is made, and output contains only skill names and
aggregate counts. JSON output includes the same limitation notice as text.

The unit is a distinct session invoking a skill by name, not the number of
times it appears in text. Claude's `sessionId` and Codex's `session_meta` ID
deduplicate copied transcript files; absent an ID, the resolved file path is
the fallback. The same skill used repeatedly in a session counts once.

Recognized invocation evidence:

- Claude user slash-command markup and literal slash/dollar skill names.
- Claude assistant `Skill` tool calls with an explicit `skill` argument.
- Codex user `response_item` records and user_message events containing a
  literal dollar/slash skill name. Repeated representations deduplicate.

Only user-message text and explicit Skill calls are examined. Ordinary
assistant narration, tool results, injected instruction catalogues, fenced
code and inline code are excluded. Short and hyphenated names both count.
User tokens and command markup are matched against names in the installed
home/project skill entrypoints and this package. This excludes unknown slash
commands and path-like prose. Historical structured Skill calls also count
without a current install, except known built-in commands unless a skill with
that name is installed. Full paths and filenames are not invocation tokens.

For a retired skill that is no longer installed, add `--skill old-skill-name`;
repeat the option for more names. This adds names to the catalogue, not a
filter on the final ranking. Only entrypoint frontmatter is read to build the
catalogue. Linked repositories are not recursively scanned.

This is a deliberately narrow measurement. Natural-language mentions without
an invocation token, skills selected by description, and Codex file reads
that do not have a preceding explicit user invocation are not counted.
Historical user invocations without an installed or explicitly supplied name
are also missed. Transcript formats can evolve. A user discussing a slash command in unquoted
prose can be a false positive; put it in code formatting to exclude it.
The command reports recognized evidence, not a definitive audit of all use.

No published productivity claim depends on this metric. Run it on your own
transcripts to see whether process skills are useful in your own work.
