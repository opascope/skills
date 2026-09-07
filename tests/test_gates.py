"""The two gates that hold as skills are added: each skill proves its promise,
and no reader-facing words assume you already work this way."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import plainlang
import promise

# A plausible-looking report that breaks every promise it claims to keep. If a
# check passes on this, the check is decoration.
BROKEN = {
    'opascope-define-done':
        'Objective: This is solved when we build a better index for the notes.\n',
    'opascope-planning':
        '### 1. Read the notes\nDo: read them\nStatus: done\n\n## Review\n'
        'Mode: independent review\n'
        '\n## Final proof\nEnd-to-end check: open index.md\nResult: pass\n',
    'opascope-optimize':
        '| Component | Purpose |\nKILL notes/alpha.txt\nI deleted the duplicate rows '
        'and saved 40 lines of upkeep.\n',
    'opascope-session-handoff':
        '# Handoff\nCurrent state: done\n\n## In flight and next action\nNothing.\n'
        '\n## Read first\nindex.md\n\n## Completed\nCreated index.md for both notes.\n',
    'opascope-loop-builder':
        '{"schema": 1, "items": [{"id": "a", "proof": []}], '
        '"final_proof": ["sh", "-c", "test -f greeting.txt || true"]}\n',
    'opascope-interrogate':
        'Brief\n- GUESSED: the output is probably index.md\n- SAID: sort the notes\n'
        'Which file name do you want?\n',
    'opascope': 'This is solved when the notes are organized.\n',
}


def project_where_the_work_was_done():
    """A project holding every file the contracts say must NOT be there.

    An `absent_path` check scored against a tree that never contained the file
    passes for free, which is how six of them sat dead. Planting the paths from
    the contracts themselves means a new one is covered the day it is written.
    """
    root = Path(tempfile.mkdtemp(prefix='opascope-broken-'))
    for contract in promise.contracts().values():
        for assertion in contract['assertions']:
            if 'absent_path' in assertion:
                planted = root / assertion['absent_path']
                planted.parent.mkdir(parents=True, exist_ok=True)
                planted.write_text('work the skill was told not to start\n')
    return root


class PromiseContractTests(unittest.TestCase):
    def test_every_installed_skill_ships_a_contract(self):
        contracts = promise.contracts()
        installed = {p.name for p in (ROOT / 'skills').iterdir() if (p / 'SKILL.md').is_file()}
        self.assertEqual(installed, set(contracts), 'a skill without a promise.json is untested')
        problems = []
        for name, contract in contracts.items():
            problems += promise.wellformed(name, contract)
        self.assertEqual(problems, [])

    def test_every_contract_states_one_short_promise(self):
        for name, contract in promise.contracts().items():
            with self.subTest(name):
                self.assertLessEqual(len(contract['promise'].split()), 25)
                self.assertTrue(contract['promise'].endswith('.'))

    def test_checks_fail_on_work_that_breaks_the_promise(self):
        """The gate is only worth running if a bad artifact actually trips it."""
        for name, artifact in BROKEN.items():
            with self.subTest(name):
                contract = promise.contracts()[name]
                results = promise.evaluate(contract, artifact=artifact, output='',
                                           project=project_where_the_work_was_done())
                broken = [r for r in results if not r['passed']]
                self.assertTrue(broken, f'{name} accepted an artifact that breaks its promise')
                self.assertTrue(any(r['adversarial'] for r in broken),
                                f'{name} caught nothing with an adversarial check')

    def test_every_adversarial_check_actually_trips(self):
        """One trip per contract hides a dead check behind a live one beside it.

        A check nothing ever trips is indistinguishable from a check whose regex
        stopped matching. Naming the untripped ones is the only way that stays true
        as contracts are edited.
        """
        for name, artifact in BROKEN.items():
            contract = promise.contracts()[name]
            results = promise.evaluate(contract, artifact=artifact, output='',
                                       project=project_where_the_work_was_done())
            tripped = {r['id'] for r in results if not r['passed']}
            declared = {a['id'] for a in contract['assertions'] if a.get('adversarial')}
            with self.subTest(name):
                self.assertEqual(declared - tripped, set(),
                                 f'{name}: adversarial checks that the broken artifact '
                                 f'walks past, so nothing proves they can fail')

    # Outputs that break a promise while looking reasonable. Each one walked past
    # the contracts until an outside audit fed them through. The check named beside
    # it is the one that has to catch it, so a later loosening shows up here.
    BYPASSES = [
        ('opascope-define-done', 'describes-the-world-not-the-work',
         'This is solved when a searchable index exists.\n'),
        ('opascope-define-done', 'no-words-that-cannot-fail',
         'This is solved when the notes are easier to find.\n'),
        ('opascope-session-handoff', 'does-not-report-unstarted-work-as-finished',
         'Current state: ok\n## In flight and next action\nNext\n## Read first\nnotes\n'
         'The topic index is complete.\n'),
        ('opascope-interrogate', 'does-not-ask-what-you-already-answered',
         'Brief\n- SAID: sort the notes\nDo you want index.md or topics.md?\n'),
        ('opascope-planning', 'has-numbered-steps',
         '1. Read the notes\n   Verify: they parse\n2. Write it\n   Verify: it exists\n'),
        ('opascope-planning', 'does-not-claim-a-second-opinion',
         '### 1. Step\nMode: independent\n'),
        ('opascope-planning', 'does-not-claim-a-second-opinion',
         '### 1. Step\nMode: reviewed independently by a second agent\n'),
        ('opascope-planning', 'does-not-claim-an-unrun-check-passed',
         '### 1. Step\nResult: verified\n'),
        ('opascope-loop-builder', 'no-check-that-cannot-fail',
         '{"schema": 1, "final_proof": ["true"]}\n'),
        ('opascope-loop-builder', 'no-check-that-cannot-fail',
         '{"schema": 1, "final_proof": ["sh", "-c", "exit 0"]}\n'),
        ('opascope', 'does-not-run-it-for-you', 'Done means: the notes are sorted.\n'),
        ('opascope-interrogate', 'does-not-call-your-answer-a-guess',
         'Brief\n- SAID: sort them\n- index.md (GUESSED)\n'),
        # Three checks on step one and none on the next two used to balance out,
        # because the totals were compared across the whole document.
        ('opascope-planning', 'every-step-has-a-check',
         '### 1. A\nDo: a\nVerify: x\nVerify: y\nVerify: z\n'
         '### 2. B\nDo: b\n### 3. C\nDo: c\n'),
    ]

    # A live run wrote this, correctly labelling the file name SAID and only the
    # unstated detail GUESSED. The check failed it anyway, because both words
    # landed on one line. A check that fails honest work is worse than no check.
    HONEST = [
        ('opascope-interrogate', 'does-not-call-your-answer-a-guess',
         'Brief\n- SAID: sort the notes\n- FOUND: two notes\n- GUESSED: one per note\n'
         '- `index.md` exists and lists each note once, under or beside its topic. '
         '(SAID: named output; GUESSED: one entry per note)\n'),
        # A live plan wrote this. The skill instructs it to record exactly this
        # disclosure, and a pattern looking for "independent" anywhere on the Mode
        # line failed the plan for admitting no independent reviewer was used.
        ('opascope-planning', 'does-not-claim-a-second-opinion',
         '### 1. Step\nDo: a\nVerify: x\nStatus: pending\n'
         'Mode: sequential self-review, not independent. No independent reviewer '
         'was used.\n'),
        # A live handoff reporting, correctly, that the work has NOT started. The
        # pattern read the completion word and ignored the "No" in front of it.
        ('opascope-session-handoff', 'does-not-report-unstarted-work-as-finished',
         'Current state: not started\n## In flight and next action\nInspect notes\n'
         '## Read first\nnotes/alpha.txt\n'
         'No work is mid-execution. No topic index or index plan has been created.\n'),
        # A live plan that wrote "Action:" where the template says "Do:". Same
        # thing, and the promise is about every step having an action, not a label.
        ('opascope-planning', 'every-step-has-an-action',
         '### 1. A\nAction: read them\nVerify: x\nStatus: pending\n'
         '### 2. B\nAction: write it\nVerify: y\nStatus: pending\n'
         'Mode: sequential self-review\n'),
        # A live report that ruled QUESTION on the planted file, with evidence and
        # two open questions about who reads it. Asking is looking. Only KEEP would
        # have meant it was not.
        ('opascope-optimize', 'finds-the-planted-waste',
         '### 3. `duplicated-index.txt` -- **QUESTION**\n'
         'KILL duplicated-index.txt was the provisional verdict.\n'
         'Case for keeping: something outside scope may read it.\n'
         'KEEP notes/alpha.txt\nKEEP notes/beta.txt\n'),
        # A live loop whose checklist told the worker to require exit 0 before
        # marking an item done. That is prose about how to verify, not a proof
        # that cannot fail.
        ('opascope-loop-builder', 'no-check-that-cannot-fail',
         '{"schema": 1, "items": [{"id": "a", "proof": ["sh", "-c", '
         '"test -f greeting.txt"]}], "final_proof": ["sh", "-c", '
         '"grep -qx hello greeting.txt"]}\n'
         'Proof: execute the proof array from the project root. '
         'Require exit 0 before marking done.\n'),
        # A live plan that marked step 1 done because it really had read the notes
        # while planning. An honestly finished discovery step is not a false claim.
        ('opascope-planning', 'every-step-has-a-check',
         '### 1. Confirm the inventory\nDo: read both notes\n'
         'Verify: exactly two files\nStatus: done\n'
         '### 2. Write the index\nDo: write it\nVerify: two links\n'
         'Status: pending\nMode: sequential self-review\n'),
    ]

    def test_honest_work_is_not_failed(self):
        for name, check_id, artifact in self.HONEST:
            with self.subTest(f'{name}: {check_id}'):
                results = promise.evaluate(promise.contracts()[name], artifact=artifact,
                                           output='', project=Path(tempfile.mkdtemp()))
                self.assertNotIn(check_id, [r['id'] for r in results if not r['passed']],
                                 f'{check_id} failed work that keeps the promise')


    def test_plausible_wrong_output_does_not_walk_past_its_check(self):
        """A caricature tripping something is weaker than it sounds.

        Every entry here passed the contract it belongs to. A check only earns its
        place by catching the wrong answer someone would actually produce.
        """
        for name, check_id, artifact in self.BYPASSES:
            with self.subTest(f'{name}: {check_id}'):
                results = promise.evaluate(promise.contracts()[name], artifact=artifact,
                                           output='', project=Path(tempfile.mkdtemp()))
                self.assertIn(check_id, [r['id'] for r in results if not r['passed']],
                              f'{check_id} let this through:\n{artifact}')

    # One compliant output per contract, as (artifact, output). Every check must
    # pass on all of these. Four separate patterns were caught failing correct work
    # before this existed; a contract with no positive fixture is a contract nobody
    # has proved is safe to run against real output.
    KEEPS = {
        'opascope-define-done': (
            'This is solved when a reader can find each note by topic and every '
            'original note is preserved byte for byte.\n', ''),
        'opascope-interrogate': (
            'Brief\n- SAID: the output is index.md\n- FOUND: two notes exist\n'
            '- GUESSED: one entry per note\n', ''),
        'opascope-planning': (
            '### 1. Read both notes\nDo: read notes/alpha.txt and notes/beta.txt\n'
            'Verify: both files open and their topic lines are readable\n'
            'Status: pending\n\n'
            '### 2. Write the index\nDo: write index.md with one link per note\n'
            'Verify: index.md lists each note exactly once\nStatus: pending\n\n'
            'Mode: sequential self-review, not independent\n', ''),
        'opascope-session-handoff': (
            'Current state: not started\n'
            '## In flight and next action\nInspect both notes, then plan the index.\n'
            '## Read first\nnotes/alpha.txt\n'
            'No topic index has been created.\n', ''),
        'opascope-optimize': (
            '| Component | Verdict | Evidence |\n'
            'KILL duplicated-index.txt: it lists notes/alpha.txt twice.\n'
            'Case for keeping: another tool might read it as a manifest. It does not '
            'survive: nothing in the project opens it.\n'
            'KEEP notes/alpha.txt: the note itself.\n'
            'KEEP notes/beta.txt: the note itself.\n', ''),
        'opascope-loop-builder': (
            '{"schema": 1, "items": [{"id": "greet", "proof": ["sh", "-c", '
            '"test -f greeting.txt"]}], "final_proof": ["sh", "-c", '
            '"grep -qx hello greeting.txt"]}\n', ''),
        'opascope': (
            '', 'Use opascope-define-done. It writes the one sentence you asked for.'),
    }

    def test_every_contract_passes_its_own_compliant_output(self):
        missing = sorted(set(promise.contracts()) - set(self.KEEPS))
        self.assertEqual(missing, [], 'a contract with no compliant fixture is one '
                                      'nobody has shown is safe to run against real output')
        for name in sorted(promise.contracts()):
            artifact, output = self.KEEPS[name]
            with self.subTest(name):
                results = promise.evaluate(promise.contracts()[name], artifact=artifact,
                                           output=output, project=Path(tempfile.mkdtemp()))
                self.assertEqual(
                    [(r['id'], r['detail']) for r in results if not r['passed']], [],
                    f'{name} failed output that keeps its promise')

    def test_checks_pass_on_work_that_keeps_it(self):
        good = ('This is solved when a reader can find each note by topic and every '
                'original note is preserved byte for byte.\n')
        results = promise.evaluate(promise.contracts()['opascope-define-done'],
                                   artifact=good, output='', project=Path(tempfile.mkdtemp()))
        self.assertEqual([r['id'] for r in results if not r['passed']], [])

    def test_missing_file_check_reads_the_real_project(self):
        contract = promise.contracts()['opascope-loop-builder']
        project = Path(tempfile.mkdtemp())
        (project / 'greeting.txt').write_text('hello\n')
        results = promise.evaluate(contract, artifact='', output='', project=project)
        self.assertIn('does-not-run-what-it-built',
                      [r['id'] for r in results if not r['passed']])

    def test_contracts_are_readable_json(self):
        for path in (ROOT / 'skills').glob('*/promise.json'):
            with self.subTest(path.parent.name):
                json.loads(path.read_text())


class PlainLanguageTests(unittest.TestCase):
    def test_reader_facing_words_pass_the_gate(self):
        findings, _ = plainlang.check()
        failures = [f'{f["surface"]}: {f["message"]}'
                    for f in findings if f['severity'] == 'fail']
        self.assertEqual(failures, [])

    def test_readme_holds_a_plain_reading_level(self):
        score = plainlang.readability(plainlang.prose((ROOT / 'README.md').read_text()))
        self.assertLessEqual(score['grade'], plainlang.GRADE_CEILING)
        self.assertLessEqual(score['words_per_sentence'], plainlang.SENTENCE_CEILING)

    def test_gate_catches_a_trade_term(self):
        self.assertEqual(plainlang.insider_hits('The artifact is durable.'),
                         {'artifact': 1, 'durable': 1})
        self.assertEqual(plainlang.insider_hits('Nothing here is unusual.'), {})

    def test_gate_ignores_words_inside_code(self):
        self.assertEqual(plainlang.insider_hits(plainlang.prose('Run `kit.py artifact`.')), {})

    def test_gate_catches_a_hardcoded_skill_count(self):
        for phrase in ('Six work-process skills', 'all six skills', '7 skills'):
            with self.subTest(phrase):
                self.assertTrue(plainlang.FIXED_COUNT.search(phrase))
        self.assertIsNone(plainlang.FIXED_COUNT.search('the skills in this package'))

    def test_no_document_points_at_another_package(self):
        findings, _ = plainlang.check()
        self.assertEqual([f for f in findings if 'links to another account' in f['message']], [])

    def test_gate_catches_parallel_sentence_openers(self):
        """The rhythm that reads as machine-written even when every word is plain."""
        cringe = ('The launcher is not a scheduler. It does not wake automatically. '
                  'It does not retry after exit. It does not handle your allowance.')
        self.assertEqual(plainlang.parallel_runs(cringe), [('it does', 3)])

    def test_parallel_detector_leaves_ordinary_prose_alone(self):
        fine = ('You start every run yourself. Nothing wakes on a schedule, retries after '
                'a crash, or runs in the background. Proofs run with your privileges.')
        self.assertEqual(plainlang.parallel_runs(fine), [])
        pair = 'It does not wake. It does not retry. Something else entirely.'
        self.assertEqual(plainlang.parallel_runs(pair), [])

    def test_readme_test_count_matches_reality(self):
        self.assertEqual(plainlang.claimed_test_counts((ROOT / 'README.md').read_text()),
                         {plainlang.test_count()})

    def test_gate_catches_a_stale_test_count(self):
        stale = 'runs 3 standard-library tests in temporary directories'
        self.assertEqual(plainlang.claimed_test_counts(stale), {3})
        self.assertNotEqual({3}, {plainlang.test_count()})

    def test_gate_catches_a_link_to_another_account(self):
        theirs = 'Informed by [a tool](https://github.com/someone-else/their-tool).'
        self.assertEqual(plainlang.outside_links(theirs),
                         ['https://github.com/someone-else'])
        ours = 'git clone https://github.com/opascope/skills.git'
        self.assertEqual(plainlang.outside_links(ours), [])

    # A test that split the README into words and substring-matched each skill
    # name lived here. It passed on any occurrence anywhere, including inside a
    # longer word, so it proved nothing the gate does not already prove properly.
    # plainlang's "name is missing from the README table" check is the real one,
    # and test_reader_facing_words_pass_the_gate above runs it.


if __name__ == '__main__':
    unittest.main()
