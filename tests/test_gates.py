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
        '### 1. Read the notes\nDo: read them\n\n## Review\nMode: independent review\n'
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
        'Brief\n- GUESSED: the output is probably index.md\n- SAID: sort the notes\n',
    'opascope': 'This is solved when the notes are organized.\n',
}


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
                results = promise.evaluate(contract, artifact=artifact, output='', project=ROOT)
                broken = [r for r in results if not r['passed']]
                self.assertTrue(broken, f'{name} accepted an artifact that breaks its promise')
                self.assertTrue(any(r['adversarial'] for r in broken),
                                f'{name} caught nothing with an adversarial check')

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

    def test_no_document_names_another_package(self):
        findings, _ = plainlang.check()
        self.assertEqual([f for f in findings if 'mentions' in f['message']], [])
        self.assertTrue(plainlang.NO_MENTION)

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

    def test_gate_catches_a_named_package(self):
        self.assertEqual(plainlang.outside_mentions('Built after reading gstack.'), ['gstack'])
        self.assertEqual(plainlang.outside_mentions('Nothing to cite here.'), [])

    def test_every_skill_is_listed_in_the_readme(self):
        table = set((ROOT / 'README.md').read_text().split())
        for path in (ROOT / 'skills').iterdir():
            if path.name != 'opascope' and (path / 'SKILL.md').is_file():
                with self.subTest(path.name):
                    self.assertIn(path.name[len('opascope-'):], ' '.join(table))


if __name__ == '__main__':
    unittest.main()
