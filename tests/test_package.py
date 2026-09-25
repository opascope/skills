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
sys.path.insert(0, str(ROOT / 'lib'))
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
        # Keep the install list out of the real checkout and out of the snapshots.
        lists = tempfile.TemporaryDirectory(prefix='skill-list-')
        self.addCleanup(lists.cleanup)
        self.index = Path(lists.name) / install.INDEX
        listing = patch.object(install, 'index_path', lambda root=None: self.index)
        listing.start()
        self.addCleanup(listing.stop)


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
                self.assertEqual(skill.read_bytes(), (ROOT / skill.parent.name / 'SKILL.md').read_bytes())
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

    def test_retired_owned_link_removed_but_changed_one_preserved(self):
        install.install(self.base, ['codex'])
        keep = install.desired_links(self.base, ['codex'])
        removed = '.agents/skills/opascope/kit.py'
        changed = '.agents/skills/opascope/shared.md'
        keep.pop(removed); keep.pop(changed)
        path = self.base / changed
        path.unlink()
        path.write_text('user-owned replacement')
        with patch.object(install, 'desired_links', return_value=keep):
            install.install(self.base, ['codex'])
        self.assertFalse((self.base / removed).is_symlink())
        self.assertEqual(path.read_text(), 'user-owned replacement')
        install.uninstall(self.base)
        self.assertEqual(path.read_text(), 'user-owned replacement')

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


    def old_layout(self):
        """Make an install look like one made before the skill folders moved to the root."""
        receipt = json.loads((self.base / install.RECEIPT).read_text())
        old = {}
        for relative, target in receipt['links'].items():
            old[relative] = str(ROOT / 'skills' / Path(target).relative_to(ROOT))
            (self.base / relative).unlink()
            (self.base / relative).symlink_to(old[relative])
        receipt['links'] = old
        (self.base / install.RECEIPT).write_text(json.dumps(receipt, indent=2) + '\n')
        return old

    def test_old_layout_links_move_to_root_layout(self):
        install.install(self.base, ['claude', 'codex'])
        fresh = snapshot(self.base)
        self.old_layout()
        install.install(self.base, ['claude', 'codex'])
        links = [p for p in self.base.rglob('*') if p.is_symlink()]
        self.assertTrue(links)
        for link in links:
            self.assertTrue(link.exists(), link)
            self.assertFalse(str(link.readlink()).startswith(str(ROOT / "skills")))
        upgraded = snapshot(self.base)
        self.assertEqual(set(upgraded), set(fresh))
        install.install(self.base, ['claude', 'codex'])
        self.assertEqual(snapshot(self.base), upgraded)

    def test_unowned_file_still_collides_after_move(self):
        install.install(self.base, ['codex'])
        self.old_layout()
        path = self.base / '.agents/skills/opascope/notes.md'
        path.write_text('user file')
        receipt = json.loads((self.base / install.RECEIPT).read_text())
        keep = install.desired_links(self.base, ['codex'])
        keep['.agents/skills/opascope/notes.md'] = str(ROOT / 'opascope/SKILL.md')
        before = snapshot(self.base)
        with patch.object(install, 'desired_links', return_value=keep):
            with self.assertRaisesRegex(ValueError, 'Collisions'):
                install.install(self.base, ['codex'])
        self.assertEqual(snapshot(self.base), before)
        self.assertNotIn('.agents/skills/opascope/notes.md', receipt['links'])

    def test_hand_repointed_link_is_not_owned(self):
        install.install(self.base, ['codex'])
        self.old_layout()
        path = self.base / '.agents/skills/opascope/SKILL.md'
        mine = self.base / 'mine.md'
        mine.write_text('mine')
        path.unlink()
        path.symlink_to(mine)
        before = snapshot(self.base)
        with self.assertRaisesRegex(ValueError, 'Collisions'):
            install.install(self.base, ['codex'])
        self.assertEqual(snapshot(self.base), before)

    def test_failed_move_restores_old_links(self):
        install.install(self.base, ['codex'])
        self.old_layout()
        before = snapshot(self.base)
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
        self.assertEqual(snapshot(self.base), before)

    def status(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            install.main(['status', *argv])
        return json.loads(output.getvalue())

    def test_project_install_shows_in_status_from_anywhere(self):
        project = self.base / 'project'
        project.mkdir()
        install.install(project, ['claude'])
        with patch.object(Path, 'home', return_value=self.base / 'elsewhere'):
            report = self.status()
        self.assertTrue(report['installed'])
        self.assertEqual(report['installs'], [{'base': str(project.resolve()), 'runtimes': ['claude']}])

    def test_uninstall_drops_off_the_list(self):
        install.install(self.base, ['codex'])
        self.assertEqual(install.read_index(), [str(self.base.resolve())])
        install.uninstall(self.base)
        self.assertEqual(install.read_index(), [])
        with patch.object(Path, 'home', return_value=self.base):
            report = self.status()
        self.assertFalse(report['installed'])
        self.assertIn('Not installed anywhere', report['note'])

    def test_listed_base_without_receipt_is_stale(self):
        gone = str(self.base / 'deleted-project')
        install.write_index([gone])
        with patch.object(Path, 'home', return_value=self.base):
            report = self.status()
        self.assertFalse(report['installed'])
        self.assertEqual(report['stale'], [gone])

    def test_install_inside_the_checkout_is_refused(self):
        package = self.base / 'package'
        (package / 'project').mkdir(parents=True)
        before = snapshot(self.base)
        with patch.object(install, 'ROOT', package):
            for base in (package, package / 'project'):
                with self.assertRaisesRegex(ValueError, 'inside the package checkout'):
                    install.install(base, ['claude'])
        self.assertEqual(snapshot(self.base), before)
        self.assertEqual(install.read_index(), [])

    def test_install_made_before_the_list_is_found_and_added(self):
        install.install(self.base, ['codex'])
        self.index.unlink()
        with patch.object(Path, 'home', return_value=self.base):
            report = self.status()
        self.assertTrue(report['installed'])
        self.assertEqual(install.read_index(), [str(self.base.resolve())])

    def test_symlinked_home_is_counted_once(self):
        install.install(self.base, ['codex'])
        alias = Path(tempfile.mkdtemp(prefix='skill-alias-')) / 'home'
        self.addCleanup(alias.parent.rmdir)
        self.addCleanup(alias.unlink)
        alias.symlink_to(self.base, target_is_directory=True)
        with patch.object(Path, 'home', return_value=alias):
            report = self.status()
        self.assertEqual([i['base'] for i in report['installs']], [str(self.base.resolve())])

    def test_concurrent_list_changes_keep_every_base(self):
        import threading
        names = [str(self.base / f'project-{n}') for n in range(40)]
        threads = [threading.Thread(target=install.change_index, kwargs={'add': [name]}) for name in names]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(install.read_index(), sorted(names))
        self.assertFalse(self.index.with_name(install.INDEX + '.lock').exists())

    def test_stuck_checkout_lock_refuses_before_changing_anything(self):
        lock = self.index.with_name(install.INDEX + '.lock')
        lock.write_text('')
        before = snapshot(self.base)
        with patch.object(install.time, 'monotonic', side_effect=[0, 100]):
            with self.assertRaisesRegex(ValueError, 'Another install, uninstall or update is running'):
                install.install(self.base, ['codex'])
        self.assertEqual(snapshot(self.base), before)
        self.assertTrue(lock.exists())

    def test_status_does_not_re_add_a_base_uninstalled_meanwhile(self):
        install.install(self.base, ['codex'])
        self.index.unlink()
        real = install.read_receipt
        calls = []
        def racing(base):
            calls.append(base)
            if len(calls) == 2:
                install.uninstall(self.base)  # lands between discovery and recording
            return real(base)
        with patch.object(Path, 'home', return_value=self.base), patch.object(install, 'read_receipt', racing):
            install.installed_bases()
        self.assertEqual(install.read_index(), [])

    def test_unrecognized_list_is_never_replaced(self):
        for body in ('someone else\'s notes', '{"bases": null}', '{"bases": ["a"]}',
                     '{"package": "opascope-skills", "schema": 1, "bases": 7}'):
            self.index.write_text(body)
            install.install(self.base, ['codex'])
            self.assertTrue((self.base / install.RECEIPT).is_file())
            self.assertEqual(self.index.read_text(), body)
            with patch.object(Path, 'home', return_value=self.base):
                report = self.status()
            self.assertTrue(report['installed'])
            self.assertIn('Unrecognized install list', report['problem'])
            install.uninstall(self.base)
            self.assertEqual(self.index.read_text(), body)

    def test_status_works_when_the_list_cannot_be_written(self):
        install.install(self.base, ['codex'])
        self.index.unlink()
        self.index.with_name(install.INDEX + '.lock').write_text('')
        with patch.object(Path, 'home', return_value=self.base), \
                patch.object(install.time, 'monotonic', side_effect=[0, 100]):
            report = self.status()
        self.assertTrue(report['installed'])

    def test_list_changes_happen_under_the_base_lock(self):
        held = []
        real = install.record
        def record(**kwargs):
            held.append((self.base / install.LOCK).exists())
            real(**kwargs)
        with patch.object(install, 'record', record):
            install.install(self.base, ['codex'])
            install.uninstall(self.base)
        self.assertEqual(held, [True, True])

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
    def test_malformed_record_shapes_do_not_abort_scan(self):
        rows = [{'type': 'user', 'message': None}, {'type': 'session_meta', 'payload': []},
                {'type': 'user', 'message': {'content': '$opascope-planning'}}]
        (self.base / 'shapes.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
        result = measurement.measure([self.base])
        self.assertEqual(result['malformed_lines'], 2)
        self.assertEqual(result['ranking'], [{'skill': 'opascope-planning', 'sessions': 1}])

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

    def test_no_progress_stops_before_budget_and_keeps_checkpoint(self):
        task = self.fixture()
        original = loops.execute
        calls = []
        def worker(argv, cwd, seconds, prompt=None):
            if prompt:
                calls.append(prompt)
                return 0, 'still considering'
            return original(argv, cwd, seconds, prompt)
        with patch.object(loops, 'execute', worker):
            self.assertEqual(loops.run(task, 'codex', 10, 10), 3)
        self.assertEqual(len(calls), 3)
        self.assertTrue(list(task.glob('pause-*.md')))
        self.assertTrue((task / 'NOTES.md').exists())

    def test_runtime_failure_is_not_completion(self):
        task = self.fixture()
        original = loops.execute
        def worker(argv, cwd, seconds, prompt=None):
            return (7, 'runtime failed') if prompt else original(argv, cwd, seconds, prompt)
        with patch.object(loops, 'execute', worker):
            self.assertEqual(loops.run(task, 'codex', 10, 10), 2)
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


class StartTests(TemporaryTest):
    def start(self, task_id=None):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            kit.start(self.base, task_id)
        return out.getvalue().splitlines()

    def task(self, *kinds):
        task = kit.new_task(self.base, 'Organize notes')
        for kind in kinds:
            kit.save(task, kind, kind + ' text')
        return task

    def test_new_project_creates_nothing(self):
        lines = self.start()
        self.assertEqual(lines[1:], ['PROJECT: new', 'NEXT: opascope-interrogate'])
        self.assertEqual(list(self.base.iterdir()), [])

    def test_next_step_follows_what_is_saved(self):
        cases = [((), 'opascope-interrogate'), (('brief',), 'opascope-define-done'),
                 (('brief', 'objective'), 'opascope-planning'),
                 (('brief', 'objective', 'plan'), 'do the work, or opascope-loop-builder to run it unattended'),
                 (('plan', 'handoff'), 'follow the latest handoff')]
        for kinds, expected in cases:
            with self.subTest(kinds=kinds):
                task = self.task(*kinds)
                self.assertEqual(self.start(task.name)[-1], 'NEXT: ' + expected)

    def test_sealed_loop_comes_before_plan(self):
        task = self.task('plan')
        (task / 'seal.json').write_text('{}')
        self.assertEqual(self.start(task.name)[-1], 'NEXT: continue the sealed loop: kit.py loop run')

    def test_handoff_only_wins_when_newest(self):
        task = self.task('handoff', 'objective')
        self.assertEqual(self.start(task.name)[-1], 'NEXT: opascope-planning')

    def test_two_tasks_list_without_choosing(self):
        first, second = self.task('brief'), self.task()
        lines = self.start()
        self.assertEqual(lines[1], 'PROJECT: known, 2 tasks')
        tasks = [line for line in lines if line.startswith('TASK: ')]
        self.assertEqual(len(tasks), 2)
        self.assertEqual([t.split()[1] for t in tasks], sorted([first.name, second.name]))
        self.assertFalse([line for line in lines if line.startswith(('SAVED:', 'NEXT:'))])

    def test_task_detail_lists_saved_paths_oldest_first(self):
        task = self.task('brief', 'objective')
        saved = [line[7:] for line in self.start(task.name) if line.startswith('SAVED: ')]
        self.assertEqual([Path(p).name.split('-')[0] for p in saved], ['brief', 'objective'])
        self.assertTrue(all(Path(p).is_absolute() and Path(p).is_file() for p in saved))
        self.assertIn('HANDOFF: none', self.start(task.name))

    def test_optimization_alone_does_not_change_next(self):
        task = self.task('optimization')
        self.assertEqual(self.start(task.name)[-1], 'NEXT: opascope-interrogate')
        self.assertIn('| saved: optimization |', self.start()[-1])

    def test_newer_local_tag_is_reported(self):
        local = tuple(map(int, (ROOT / 'VERSION').read_text().split('.')))
        newer = (local[0], local[1] + 1, 0)
        with patch.object(kit, 'local_versions', return_value=[local, newer]):
            first = self.start()[0]
        self.assertEqual(first, 'PACKAGE: %s, v%d.%d.%d available (update only when the user asks)' % (('.'.join(map(str, local)),) + newer))

    def test_failed_version_lookup_still_reports(self):
        with patch.object(kit, 'git', side_effect=subprocess.SubprocessError('no git')):
            lines = self.start()
        self.assertEqual(lines[0], 'PACKAGE: %s (update check unavailable)' % (ROOT / 'VERSION').read_text().strip())
        self.assertEqual(lines[1:], ['PROJECT: new', 'NEXT: opascope-interrogate'])

    def test_owned_directory_without_tasks(self):
        kit.artifact_root(self.base, create=True)
        self.assertEqual(self.start()[1:], ['PROJECT: known, 0 tasks', 'NEXT: opascope-interrogate'])

    def test_task_detail_names_latest_handoff(self):
        task = self.task('brief', 'handoff')
        handoff = next(task.glob('handoff-*.md'))
        lines = self.start(task.name)
        self.assertIn('HANDOFF: %s' % handoff, lines)
        self.assertIn('SAVED: %s' % handoff, lines)
        self.assertEqual(lines[-1], 'NEXT: follow the latest handoff')


class PackageTests(unittest.TestCase):
    def test_every_skill_has_metadata_and_resolving_markdown_links(self):
        skills = list(ROOT.glob('opascope*/SKILL.md'))
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
