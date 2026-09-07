# Changelog

## 0.3.0

- Checks rewritten after an outside audit fed them plausible wrong answers. Most
  were phrase lists a reasonable wrong output walked past, and six could never
  fail at all. Every adversarial check must now trip on a broken artifact.
- Per-step checks hold per step. Comparing totals across a plan let three checks
  on one step cover two steps with none.
- Every contract carries a compliant output that all of its checks must pass.
  Running the skills for real found eight checks that failed work which kept the
  promise, including a plan penalised for admitting it had no independent
  reviewer. It found no skill breaking a promise.
- Final runs: every skill plus the router on both runtimes, all 36 checks held
  on each, fixtures unchanged. See docs/verification.md.
- The outputs that once slipped past, and the correct ones once wrongly failed,
  ship as tests.
- The router's promise said it points you at a skill "instead of picking one for
  you", which is the opposite of its job. It was exempt from the gate that would
  have caught that. It is not exempt now.
- The plain-language gate held the names it banned, so shipping it published
  them. It checks for links to other accounts instead.
- GitHub Actions runs the tests, the contract check and the language gate on
  every push, on the oldest and newest supported Python.

## 0.2.0

- Per-skill promise contracts with adversarial checks, checked offline against
  fabricated outputs and one live run of a single skill.
- Live runtime cases now come from the contracts, so adding a skill edits no test.
- Plain-language gate on skill names, promises and the README.
- README rewritten in plain words against that gate, and given headings
  written as the questions a reader actually asks.

## 0.1.0

- Six portable work-process skills and a router for Claude Code and Codex CLI.
- Shared project-local artifacts and explicit cold-session pickup.
- Interactive link installation, ownership receipts and reversible uninstall.
- Bounded foreground loops with independently executed proofs.
- Offline usage ranking and explicit versioned updates.
