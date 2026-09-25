"""Exercise version updates against a local Git remote, without network access."""
import contextlib
import io
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
import json
import kit


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='skill-update-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.remote = self.base / 'remote.git'
        self.author = self.base / 'author'
        self.reader = self.base / 'reader'
        self.git(self.base, 'init', '--bare', str(self.remote))
        self.git(self.base, 'clone', str(self.remote), str(self.author))
        self.git(self.author, 'checkout', '-b', 'main')
        self.git(self.author, 'config', 'user.name', 'Example')
        self.git(self.author, 'config', 'user.email', 'example@example.invalid')
        (self.author / 'VERSION').write_text('0.1.0\n')
        self.git(self.author, 'add', 'VERSION')
        self.git(self.author, 'commit', '-m', 'first')
        self.git(self.author, 'tag', 'v0.1.0')
        self.git(self.author, 'push', '-u', 'origin', 'main', '--tags')
        self.git(self.base, 'clone', '--branch', 'main', str(self.remote), str(self.reader))
        (self.author / 'VERSION').write_text('0.2.0\n')
        (self.author / 'CHANGELOG.md').write_text('# Changelog\n\n## 0.2.0\n\n- The new thing.\n\n## 0.1.0\n\n- The first thing.\n')
        self.git(self.author, 'add', 'CHANGELOG.md')
        self.git(self.author, 'commit', '-am', 'second')
        self.git(self.author, 'tag', 'v0.2.0')
        self.git(self.author, 'push', 'origin', 'main', '--tags')

    def git(self, directory, *args):
        return subprocess.run(['git', '-C', str(directory), *args], check=True, capture_output=True, text=True).stdout

    def test_check_is_read_only_then_update_fast_forwards(self):
        before = self.git(self.reader, 'rev-parse', 'HEAD')
        with patch.object(kit, 'ROOT', self.reader), contextlib.redirect_stdout(io.StringIO()) as output:
            kit.update(check=True)
            self.assertEqual(self.git(self.reader, 'rev-parse', 'HEAD'), before)
            self.assertIn('v0.2.0', output.getvalue())
            kit.update()
        self.assertEqual((self.reader / 'VERSION').read_text(), '0.2.0\n')
        self.assertEqual(self.git(self.reader, 'rev-parse', 'HEAD'), self.git(self.author, 'rev-parse', 'HEAD'))

    def test_update_prints_whats_new(self):
        with patch.object(kit, 'ROOT', self.reader), contextlib.redirect_stdout(io.StringIO()) as output:
            kit.update()
        text = output.getvalue()
        self.assertIn("What's new:", text)
        self.assertIn('- The new thing.', text)
        self.assertNotIn('The first thing.', text)
        self.assertNotIn('## 0.1.0', text)

    def test_dirty_checkout_preserved(self):
        (self.reader / 'personal.txt').write_text('uncommitted')
        with patch.object(kit, 'ROOT', self.reader), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'local changes'):
                kit.update()
        self.assertEqual((self.reader / 'VERSION').read_text(), '0.1.0\n')
        self.assertEqual((self.reader / 'personal.txt').read_text(), 'uncommitted')

    def test_offline_check_uses_only_local_tags(self):
        self.git(self.reader, 'remote', 'remove', 'origin')
        with patch.object(kit, 'ROOT', self.reader), contextlib.redirect_stdout(io.StringIO()) as output:
            kit.update(check=True, offline=True)
        self.assertEqual(output.getvalue(), '')

    def test_divergent_history_is_not_overwritten(self):
        self.git(self.reader, 'config', 'user.name', 'Example')
        self.git(self.reader, 'config', 'user.email', 'example@example.invalid')
        (self.reader / 'local.txt').write_text('local commit')
        self.git(self.reader, 'add', 'local.txt')
        self.git(self.reader, 'commit', '-m', 'local change')
        before = self.git(self.reader, 'rev-parse', 'HEAD')
        with patch.object(kit, 'ROOT', self.reader), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'diverges'):
                kit.update()
        self.assertEqual(self.git(self.reader, 'rev-parse', 'HEAD'), before)


    def test_update_refreshes_every_listed_install(self):
        bases = [self.base / 'home', self.base / 'project']
        for base in bases:
            base.mkdir()
            (base / install.RECEIPT).write_text(json.dumps({
                'package': 'opascope-skills', 'schema': 1, 'source': str(self.reader),
                'runtimes': ['claude'], 'links': {}, 'directories': []}))
        (self.reader / install.INDEX).write_text(json.dumps({'package': 'opascope-skills', 'schema': 1, 'bases': [str(bases[1].resolve())]}))
        with (self.reader / '.git/info/exclude').open('a') as stream:
            stream.write(install.INDEX + '\n')
        calls = []
        real_run = subprocess.run
        def run(command, *args, **kwargs):
            if str(command[1]).endswith('install.py'):
                calls.append(command[command.index('--base') + 1])
                return subprocess.CompletedProcess(command, 0)
            return real_run(command, *args, **kwargs)
        with patch.object(kit, 'ROOT', self.reader), patch.object(Path, 'home', return_value=bases[0]), \
                patch.object(kit.subprocess, 'run', run), contextlib.redirect_stdout(io.StringIO()):
            kit.update()
        self.assertEqual(sorted(calls), sorted(str(b.resolve()) for b in bases))

    def test_named_update_does_not_claim_every_install(self):
        base = self.base / 'project'
        base.mkdir()
        (base / install.RECEIPT).write_text(json.dumps({
            'package': 'opascope-skills', 'schema': 1, 'source': str(self.reader),
            'runtimes': ['claude'], 'links': {}, 'directories': []}))
        real_run = subprocess.run
        def run(command, *args, **kwargs):
            if str(command[1]).endswith('install.py'):
                return subprocess.CompletedProcess(command, 0)
            return real_run(command, *args, **kwargs)
        with patch.object(kit, 'ROOT', self.reader), patch.object(kit.subprocess, 'run', run), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            kit.update(bases=[base])
        self.assertIn('Refreshed the installations you named', output.getvalue())
        self.assertNotIn('every installation', output.getvalue())

    def test_one_failed_relink_does_not_skip_the_rest(self):
        bases = [self.base / 'first', self.base / 'second']
        for base in bases:
            base.mkdir()
            (base / install.RECEIPT).write_text(json.dumps({
                'package': 'opascope-skills', 'schema': 1, 'source': str(self.reader),
                'runtimes': ['claude'], 'links': {}, 'directories': []}))
        calls = []
        real_run = subprocess.run
        def run(command, *args, **kwargs):
            if str(command[1]).endswith('install.py'):
                calls.append(command[command.index('--base') + 1])
                return subprocess.CompletedProcess(command, 1 if len(calls) == 1 else 0)
            return real_run(command, *args, **kwargs)
        with patch.object(kit, 'ROOT', self.reader), patch.object(kit.subprocess, 'run', run), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(ValueError, 'Not refreshed') as caught:
                kit.update(bases=bases)
        self.assertEqual(calls, [str(b) for b in bases])
        self.assertIn('1 installation(s) were not refreshed', output.getvalue())
        self.assertIn(f'--base "{bases[0]}" --runtime claude --yes', str(caught.exception))
        self.assertIn("What's new:", output.getvalue())

    def test_unreadable_list_stops_update_before_the_checkout_moves(self):
        (self.reader / install.INDEX).write_text('not ours')
        with (self.reader / '.git/info/exclude').open('a') as stream:
            stream.write(install.INDEX + '\n')
        before = self.git(self.reader, 'rev-parse', 'HEAD')
        with patch.object(kit, 'ROOT', self.reader), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'Unrecognized install list'):
                kit.update()
        self.assertEqual(self.git(self.reader, 'rev-parse', 'HEAD'), before)
        self.assertEqual((self.reader / install.INDEX).read_text(), 'not ours')

    def test_missing_listed_install_stops_update_before_the_checkout_moves(self):
        gone = str(self.base / 'unplugged-drive')
        (self.reader / install.INDEX).write_text(json.dumps({'package': 'opascope-skills', 'schema': 1, 'bases': [gone]}))
        with (self.reader / '.git/info/exclude').open('a') as stream:
            stream.write(install.INDEX + '\n')
        before = self.git(self.reader, 'rev-parse', 'HEAD')
        with patch.object(kit, 'ROOT', self.reader), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'not found, so nothing was updated'):
                kit.update()
        self.assertEqual(self.git(self.reader, 'rev-parse', 'HEAD'), before)
        with patch.object(install, 'ROOT', self.reader), contextlib.redirect_stdout(io.StringIO()):
            install.main(['forget', '--base', gone])
        self.assertEqual(install.read_index(self.reader), [])

    def test_install_waits_while_an_update_holds_the_checkout(self):
        base = self.base / 'project'
        base.mkdir()
        (base / install.RECEIPT).write_text(json.dumps({
            'package': 'opascope-skills', 'schema': 1, 'source': str(self.reader),
            'runtimes': ['codex'], 'links': {}, 'directories': []}))
        lock = str(install.index_path(self.reader).with_name(install.INDEX + '.lock'))
        with (self.reader / '.git/info/exclude').open('a') as stream:
            stream.write(install.INDEX + '*\n')
        seen = []
        real_run = subprocess.run
        def run(command, *args, **kwargs):
            if str(command[1]).endswith('install.py'):
                # The lock is held for the whole update and handed to the installer it starts.
                seen.append((Path(lock).exists(), kwargs['env']['OPASCOPE_CHECKOUT_LOCKED'] == lock))
                return subprocess.CompletedProcess(command, 0)
            return real_run(command, *args, **kwargs)
        with patch.object(kit, 'ROOT', self.reader), patch.object(Path, 'home', return_value=base), \
                patch.object(kit.subprocess, 'run', run), contextlib.redirect_stdout(io.StringIO()):
            kit.update()
        self.assertEqual(seen, [(True, True)])
        self.assertFalse(Path(lock).exists())

if __name__ == '__main__':
    unittest.main()
