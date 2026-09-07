import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import install
import kit
import loops
import measurement


def snapshot(base):
    return {str(p.relative_to(base)): ('link', str(p.readlink())) if p.is_symlink()
            else ('dir',) if p.is_dir() else ('file', hashlib.sha256(p.read_bytes()).hexdigest())
            for p in base.rglob('*')}


class TemporaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='skill-test-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)


class InstallerTests(TemporaryTest):
    def test_clean_both_idempotent_and_exact_uninstall(self):
        (self.base / 'personal.txt').write_text('keep')
        before = snapshot(self.base)
        install.install(self.base, ['claude', 'codex'])
        installed = snapshot(self.base)
        for location in install.LOCATIONS.values():
            skills = list((self.base / location).glob('*/SKILL.md'))
            self.assertEqual(len(skills), 7)
            for skill in skills:
                self.assertEqual(skill.read_bytes(), (ROOT / 'skills' / skill.parent.name / 'SKILL.md').read_bytes())
                self.assertTrue((skill.parent / 'kit.py').resolve().samefile(ROOT / 'kit.py'))
                self.assertTrue((skill.parent / 'shared.md').is_file())
        install.install(self.base, ['claude', 'codex'])
        self.assertEqual(snapshot(self.base), installed)
        install.uninstall(self.base)
        self.assertEqual(snapshot(self.base), before)

    def test_preexisting_empty_skill_directory_is_collision(self):
        target = self.base / '.agents/skills/opascope-planning'
        target.mkdir(parents=True)
        before = snapshot(self.base)
        with self.assertRaisesRegex(ValueError, 'Collisions'):
            install.install(self.base, ['claude', 'codex'])
        self.assertEqual(before, snapshot(self.base))

    def test_dangling_link_and_parent_link_are_collisions(self):
        target = self.base / '.claude'
        target.symlink_to(self.base / 'absent')
        before = snapshot(self.base)
        with self.assertRaises(ValueError):
            install.install(self.base, ['claude'])
        self.assertEqual(before, snapshot(self.base))

    def test_uninstall_preserves_user_additions_and_replaced_files(self):
        install.install(self.base, ['codex'])
        target = self.base / '.agents/skills/opascope/SKILL.md'
        target.unlink()
        target.write_text('user replacement')
        extra = target.parent / 'personal.md'
        extra.write_text('personal')
        install.uninstall(self.base)
        self.assertEqual(target.read_text(), 'user replacement')
        self.assertEqual(extra.read_text(), 'personal')
        self.assertFalse((self.base / install.RECEIPT).exists())

    def test_preexisting_runtime_directories_survive(self):
        (self.base / '.agents/skills').mkdir(parents=True)
        before = snapshot(self.base)
        install.install(self.base, ['codex'])
        install.uninstall(self.base)
        self.assertEqual(before, snapshot(self.base))

    def test_uninstall_does_not_follow_replaced_parent(self):
        install.install(self.base, ['codex'])
        parent = self.base / '.agents'
        moved = self.base / 'moved'
        parent.rename(moved)
        parent.symlink_to(moved, target_is_directory=True)
        before = snapshot(moved)
        install.uninstall(self.base)
        self.assertEqual(snapshot(moved), before)

    def test_interactive_install_cancel_and_install(self):
        with patch('builtins.input', side_effect=['1', 'n']):
            install.main(['--base', str(self.base)])
        self.assertEqual(snapshot(self.base), {})
        with patch('builtins.input', side_effect=['3', 'y']):
            install.main(['--base', str(self.base)])
        self.assertTrue((self.base / '.agents/skills/opascope/SKILL.md').is_file())
        self.assertFalse((self.base / '.claude').exists())

    def test_partial_install_rolls_back(self):
        original = Path.symlink_to
        calls = []
        def flaky(path, *args, **kwargs):
            calls.append(path)
            if len(calls) == 4:
                raise OSError('simulated filesystem error')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'symlink_to', flaky):
            with self.assertRaises(OSError):
                install.install(self.base, ['codex'])
        self.assertEqual(snapshot(self.base), {})


class ArtifactTests(TemporaryTest):
    def test_project_move_preserves_pickup(self):
        original = self.base / 'original'
        original.mkdir()
        task = kit.new_task(original, 'portable')
        kit.save(task, 'handoff', 'resume the next action')
        moved = self.base / 'moved'
        original.rename(moved)
        self.assertEqual(kit.resolve_task(moved, task.name).name, task.name)
        self.assertEqual(kit.artifact_root(moved)[0], moved)

    def test_zero_config_non_git_nested_and_two_projects(self):
        a, b = self.base / 'a', self.base / 'b'
        a.mkdir(); b.mkdir()
        task = kit.new_task(a, 'Note sorting')
        nested = a / 'nested'
        nested.mkdir()
        self.assertEqual(kit.artifact_root(nested)[1], task.parent)
        other = kit.new_task(b, 'Note sorting')
        self.assertNotEqual(task.parent, other.parent)
        another = kit.new_task(a, 'Note sorting')
        self.assertNotEqual(task, another)

    def test_git_root_and_config_override(self):
        subprocess.run(['git', 'init', '-q', str(self.base)], check=True)
        nested = self.base / 'nested'
        nested.mkdir()
        (self.base / kit.CONFIG).write_text('{"artifact_dir":"notes/artifacts"}')
        task = kit.new_task(nested, 'work')
        self.assertEqual(task.parent, self.base / 'notes/artifacts')

    def test_existing_unowned_root_refused(self):
        (self.base / '.opascope-work').mkdir()
        with self.assertRaisesRegex(ValueError, 'Unowned'):
            kit.new_task(self.base, 'work')

    def test_shared_absolute_override_refused(self):
        a, b = self.base / 'a', self.base / 'b'
        a.mkdir(); b.mkdir()
        config = json.dumps({'artifact_dir': str(self.base / 'shared')})
        (a / kit.CONFIG).write_text(config); (b / kit.CONFIG).write_text(config)
        kit.new_task(a, 'work')
        with self.assertRaisesRegex(ValueError, 'another project'):
            kit.new_task(b, 'work')

    def test_handoff_preserves_prior_and_cold_resume(self):
        task = kit.new_task(self.base, 'work')
        first = kit.save(task, 'handoff', 'first checkpoint')
        second = kit.save(task, 'handoff', 'second checkpoint')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            kit.resume(self.base, task.name)
        self.assertIn('second checkpoint', out.getvalue())
        self.assertIn(str(second), out.getvalue())
        self.assertEqual(first.read_text(), 'first checkpoint\n')
        with self.assertRaises(ValueError):
            kit.resolve_task(self.base, '../escape')

    def test_read_only_resume_creates_nothing(self):
        kit.resume(self.base)
        self.assertEqual(snapshot(self.base), {})


class MeasurementTests(TemporaryTest):
    def test_catalog_filters_builtin_markup_and_unknown_prose(self):
        record = {'type': 'user', 'message': {'content': '<command-name>/model</command-name> /copy /unknown $opascope-planning'}}
        self.assertEqual(measurement.invocations(record, {'opascope-planning'}), {'opascope-planning'})
        record = {'type': 'user', 'message': {'content': '/old-skill'}}
        self.assertEqual(measurement.invocations(record, {'old-skill'}), {'old-skill'})

    def test_short_names_and_paths(self):
        record = {'type': 'user', 'message': {'content': '$planning /interrogate /help /tmp/file.py /a-file.md'}}
        self.assertEqual(measurement.invocations(record), {'planning', 'interrogate'})

    def test_session_dedup_and_invocation_only(self):
        rows = [
            {'type': 'session_meta', 'payload': {'id': 'test-session'}},
            {'type': 'response_item', 'payload': {'role': 'user', 'content': [{'type': 'input_text', 'text': '$opascope-planning do this'}]}},
            {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': '$opascope-planning do this'}},
            {'type': 'response_item', 'payload': {'role': 'assistant', 'content': [{'type': 'output_text', 'text': '$opascope-optimize mentioned'}]}},
            {'type': 'user', 'message': {'content': '`$opascope-optimize` and ```\n/opascope-interrogate\n```'}},
            {'type': 'user', 'message': {'content': '<INSTRUCTIONS> /opascope-optimize </INSTRUCTIONS>'}},
        ]
        text = '\n'.join(json.dumps(x) for x in rows)
        (self.base / 'one.jsonl').write_text(text + '\ninvalid\n')
        (self.base / 'copy.jsonl').write_text(text)
        before = snapshot(self.base)
        result = measurement.measure([self.base])
        self.assertEqual(result['ranking'], [{'skill': 'opascope-planning', 'sessions': 1}])
        self.assertEqual(result['sessions'], 1)
        self.assertEqual(result['malformed_lines'], 1)
        self.assertIn('Undercounts', result['limitation'])
        self.assertEqual(before, snapshot(self.base))

    def test_claude_skill_call_and_command(self):
        rows = [
            {'type': 'user', 'sessionId': 'toy', 'message': {'content': '<command-name>/opascope</command-name>'}},
            {'type': 'assistant', 'sessionId': 'toy', 'message': {'content': [{'type': 'tool_use', 'name': 'Skill', 'input': {'skill': 'opascope-planning'}}]}},
        ]
        (self.base / 'session.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
        self.assertEqual(len(measurement.measure([self.base])['ranking']), 2)


class LoopTests(TemporaryTest):
    def fixture(self):
        task = kit.new_task(self.base, 'loop')
        for name in ('CONTEXT.md', 'SAFETY.md', 'init.md', 'CHECKLIST.md', 'NOTES.md', 'BLOCKERS.md'):
            (task / name).write_text('# ' + name + '\n')
        proof = [sys.executable, '-c', "from pathlib import Path; assert Path('result.txt').read_text() == 'ok'"]
        data = {'schema': 1, 'objective': 'result.txt equals ok',
                'items': [{'id': 'one', 'description': 'produce result', 'depends': [], 'proof': proof}],
                'final_proof': proof, 'outputs': ['result.txt'], 'verifier_files': []}
        (task / 'loop.json').write_text(json.dumps(data))
        loops.seal(task)
        return task

    def test_real_proofs_reject_worker_claim_and_accept_actual_output_both_adapters(self):
        for runtime in ('claude', 'codex'):
            task = self.fixture()
            def worker(argv, cwd, seconds, prompt=None):
                if prompt is not None:
                    (self.base / 'result.txt').write_text('ok')
                    return 0, 'completed'
                return original(argv, cwd, seconds, prompt)
            original = loops.execute
            with patch.object(loops, 'execute', worker):
                self.assertEqual(loops.run(task, runtime, 1, 10), 0)
            self.assertTrue(list(task.glob('completion-*.md')))
            (self.base / 'result.txt').unlink()

    def test_narrated_success_without_proof_is_not_done(self):
        task = self.fixture()
        original = loops.execute
        def worker(argv, cwd, seconds, prompt=None):
            return (0, 'all done') if prompt else original(argv, cwd, seconds, prompt)
        with patch.object(loops, 'execute', worker):
            self.assertEqual(loops.run(task, 'codex', 1, 10), 3)
        self.assertFalse(list(task.glob('completion-*.md')))

    def test_blocker_and_mutation_stop(self):
        task = self.fixture()
        (task / 'BLOCKERS.md').write_text('- BLOCKED: missing input\n')
        self.assertEqual(loops.run(task, 'claude', 1, 10), 2)
        (task / 'BLOCKERS.md').write_text('# Blockers\n')
        (task / 'CONTEXT.md').write_text('weakened goal')
        with self.assertRaisesRegex(ValueError, 'Criteria'):
            loops.run(task, 'codex', 1, 10)

    def test_drift_during_call_stops(self):
        task = self.fixture()
        original = loops.execute
        def worker(argv, cwd, seconds, prompt=None):
            if prompt:
                (task / 'loop.json').write_text('{}')
                return 0, 'done'
            return original(argv, cwd, seconds, prompt)
        with patch.object(loops, 'execute', worker):
            with self.assertRaisesRegex(ValueError, 'Criteria changed'):
                loops.run(task, 'codex', 1, 10)
        self.assertFalse((task / 'run.lock').exists())

    def test_resume_success_and_lock(self):
        task = self.fixture()
        (task / 'run.lock').write_text('existing')
        with self.assertRaisesRegex(ValueError, 'already running'):
            loops.run(task, 'codex', 1, 10)
        (task / 'run.lock').unlink()
        (self.base / 'result.txt').write_text('ok')
        with patch.object(loops, 'runtime_command', side_effect=AssertionError('must not launch')):
            self.assertEqual(loops.run(task, 'codex', 1, 10), 0)

    def test_timeout_process_is_stopped(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            loops.execute([sys.executable, '-c', 'import time; time.sleep(30)'], self.base, 0.05)


class PackageTests(unittest.TestCase):
    def test_every_skill_has_metadata_and_resolving_markdown_links(self):
        skills = list((ROOT / 'skills').glob('*/SKILL.md'))
        self.assertEqual(len(skills), 7)
        import re
        for path in skills:
            text = path.read_text()
            self.assertTrue(text.startswith('---\nname: ' + path.parent.name + '\n'))
            for required in ('description:', 'allowed-tools:', 'claude: full', 'codex: full'):
                self.assertIn(required, text)
            for relative in re.findall(r'\]\(([^)]+)\)', text):
                self.assertTrue((path.parent / relative).exists(), relative)

    def test_numeric_version_order(self):
        self.assertEqual(kit.versions('refs/tags/v0.9.0\nrefs/tags/v0.10.0\nrefs/tags/v0.11.0-rc1'), [(0, 9, 0), (0, 10, 0)])


if __name__ == '__main__':
    unittest.main()
