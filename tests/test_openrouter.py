import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'openrouter' / 'scripts'))
import openrouter

FAKE_KEY = 'sk-or-v1-fake0123456789abcdef'


class Reply:
    def __init__(self, data):
        self.body = json.dumps(data).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def answer(model, text='391', usage=None, finish='stop', **extra):
    data = {'id': 'gen-test123', 'model': model,
            'choices': [{'finish_reason': finish, 'message': {'content': text}}],
            'usage': usage if usage is not None else {'cost': 0.0000384, 'prompt_tokens': 10, 'completion_tokens': 3}}
    data.update(extra)
    return data


class OpenRouterTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'OPENROUTER_API_KEY': FAKE_KEY})
        environment.start()
        self.addCleanup(environment.stop)
        for name in list(os.environ):
            if name.startswith('OPENROUTER_MODEL_'):
                del os.environ[name]
        self.sent = []
        self.replies = []

    def fake(self, request, timeout=None):
        body = json.loads(request.data.decode()) if request.data else None
        self.sent.append((request.full_url, body))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return Reply(reply)

    def run_cli(self, *argv, key=True):
        out, err = io.StringIO(), io.StringIO()
        env = {} if key else {'OPENROUTER_API_KEY': ''}
        with patch.object(openrouter, 'urlopen', self.fake), patch.dict(os.environ, env), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = openrouter.main(list(argv))
        self.assertNotIn(FAKE_KEY, out.getvalue())
        self.assertNotIn(FAKE_KEY, err.getvalue())
        self.assertNotIn('sk-or-', out.getvalue() + err.getvalue())
        return code, out.getvalue(), err.getvalue()

    def roles(self):
        return openrouter.load_roles()[0]

    def test_no_model_uses_router_and_names_the_answering_model(self):
        self.replies = [answer('deepseek/deepseek-v4.1-flash')]
        code, out, err = self.run_cli('chat', 'What is 17 times 23?')
        self.assertEqual(code, 0)
        self.assertEqual(self.sent[0][1]['model'], self.roles()['auto']['model'])
        self.assertIn('model: deepseek/deepseek-v4.1-flash | chosen by: jev-router', err)
        self.assertNotIn('model: ' + self.roles()['auto']['model'], err)

    def test_model_beats_role_beats_router(self):
        self.replies = [answer('x/one'), answer('x/two')]
        self.run_cli('chat', 'hi', '--role', 'frontier', '--model', 'x/one')
        self.assertEqual(self.sent[0][1]['model'], 'x/one')
        code, _, err = self.run_cli('chat', 'hi', '--role', 'frontier')
        self.assertEqual(self.sent[1][1]['model'], self.roles()['frontier']['model'])
        self.assertIn('chosen by: role frontier', err)

    def test_research_uses_sonar_and_numbers_citations(self):
        self.replies = [answer('perplexity/sonar', 'Tokyo is the capital.',
                               citations=['https://example.org/a', 'https://example.org/b'])]
        code, out, err = self.run_cli('research', 'What is the capital of Japan?')
        self.assertEqual(self.sent[0][1]['model'], self.roles()['sonar']['model'])
        self.assertIn('[1] https://example.org/a', out)
        self.assertIn('[2] https://example.org/b', out)

    def test_byok_reports_upstream_cost(self):
        usage = {'cost': 0, 'is_byok': True, 'cost_details': {'upstream_inference_cost': 0.0000144},
                 'prompt_tokens': 5, 'completion_tokens': 2}
        self.replies = [answer('openai/gpt-6-luna', usage=usage)]
        _, _, err = self.run_cli('chat', 'hi')
        self.assertIn('cost: $0.0000144', err)

    def test_missing_cost_is_unknown(self):
        self.replies = [answer('x/one', usage={'prompt_tokens': 1, 'completion_tokens': 1})]
        _, _, err = self.run_cli('chat', 'hi')
        self.assertIn('cost: unknown', err)

    def test_empty_reply_retries_once_then_names_the_model(self):
        empty = answer('x/thinker', text='', finish='length')
        self.replies = [empty, empty]
        code, _, err = self.run_cli('chat', 'hi', '--model', 'x/thinker', '--max-tokens', '16')
        self.assertEqual(code, 1)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.sent[1][1]['max_tokens'], 32)
        self.assertEqual(self.sent[1][1]['reasoning'], {'enabled': False})
        self.assertIn('x/thinker', err)

    def test_no_key_names_the_variable_and_models_still_work(self):
        code, _, err = self.run_cli('chat', 'hi', key=False)
        self.assertEqual(code, 1)
        self.assertIn('OPENROUTER_API_KEY', err)
        self.assertEqual(self.sent, [])
        code, out, _ = self.run_cli('models', key=False)
        self.assertEqual(code, 0)
        self.assertIn('auto', out)

    def test_key_never_appears_in_any_output(self):
        self.replies = [answer('x/one'), urllib.error.HTTPError('u', 401, 'no', {}, io.BytesIO(
            json.dumps({'error': {'message': 'bad key ' + FAKE_KEY}}).encode()))]
        with tempfile.TemporaryDirectory() as tmp:
            receipt = Path(tmp) / 'receipt.txt'
            code, out, err = self.run_cli('chat', 'hi', '--json-out', '--receipt', str(receipt))
            self.assertEqual(code, 0)
            self.assertNotIn(FAKE_KEY, receipt.read_text())
            self.assertIn('gen-test123', receipt.read_text())
            self.assertEqual(json.loads(out)['model'], 'x/one')
        code, _, err = self.run_cli('chat', 'hi')
        self.assertEqual(code, 1)
        self.assertIn('401', err)

    def test_json_goes_to_fast_when_the_router_does_not_honor_it(self):
        self.replies = [answer('x/fast', text='{}')]
        real = openrouter.load_roles
        with patch.object(openrouter, 'load_roles', lambda: (real()[0], {'json': False, 'image': True})):
            _, _, err = self.run_cli('chat', 'hi', '--json')
        self.assertEqual(self.sent[0][1]['model'], self.roles()['fast']['model'])
        self.assertIn('chosen by: role fast (router does not support --json)', err)

    def test_models_check_flags_a_missing_role(self):
        present = [{'id': r['model']} for n, r in self.roles().items() if n != 'writer']
        self.replies = [{'data': present}, {'data': []}]
        code, out, _ = self.run_cli('models', 'check', key=False)
        self.assertEqual(code, 1)
        self.assertIn('writer: missing', out)
        self.assertIn('decision: skipped (decisions endpoint)', out)


class LiveSkipTests(unittest.TestCase):
    def test_contract_needing_an_unset_variable_is_skipped(self):
        sys.path.insert(0, str(ROOT / 'tests'))
        import live_runtime
        import promise
        contract = promise.contracts()['openrouter']
        self.assertEqual(live_runtime.missing_env(contract, {}), ['OPENROUTER_API_KEY'])
        self.assertEqual(live_runtime.missing_env(contract, {'OPENROUTER_API_KEY': 'set'}), [])
        self.assertEqual(live_runtime.missing_env(promise.contracts()['planning'], {}), [])


if __name__ == '__main__':
    unittest.main()
