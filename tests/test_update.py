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


if __name__ == '__main__':
    unittest.main()
