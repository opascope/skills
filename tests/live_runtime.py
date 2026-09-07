"""Opt-in real CLI checks. Creates isolated toy projects and retains local evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import install
import kit
import loops
import promise

SEED = {
    'notes/alpha.txt': 'Topic: trees\nLeaves shade the path.\n',
    'notes/beta.txt': 'Topic: water\nRain fills the pond.\n',
    'duplicated-index.txt': 'notes/alpha.txt\nnotes/alpha.txt\nnotes/beta.txt\n',
}


def final_text(output):
    """The model's own last words, not the tool trace around them.

    A check that reads the whole JSONL matches the skill's own table and
    instructions echoed back inside the trace, so it passes without the run
    having said anything. Only the final answer is evidence of what was claimed.
    Both runtimes are handled: an explicit result event wins, otherwise the
    assistant text blocks are joined in order.
    """
    spoken = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if isinstance(event.get('result'), str):
            return event['result']
        message = event.get('message')
        if isinstance(message, dict) and message.get('role') == 'assistant':
            for block in message.get('content') or []:
                if isinstance(block, dict) and block.get('type') == 'text':
                    spoken.append(block.get('text', ''))
        item = event.get('item')
        if isinstance(item, dict) and item.get('type') == 'agent_message':
            spoken.append(item.get('text', ''))
    return '\n'.join(spoken)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', choices=['claude', 'codex'], required=True)
    parser.add_argument('--only', choices=sorted(promise.contracts()))
    parser.add_argument('--seconds', type=int, default=300)
    args = parser.parse_args()
    evidence = kit.new_task(ROOT, 'live-' + args.runtime)
    print(f'Evidence: {evidence}', flush=True)
    results = []
    for name, contract in sorted(promise.contracts().items()):
        if args.only and args.only != name:
            continue
        request = contract['fixture']['request']
        project = Path(tempfile.mkdtemp(prefix='opascope-live-' + args.runtime + '-' + name + '-'))
        (evidence / (name + '-project.txt')).write_text(str(project) + '\n')
        (project / 'notes').mkdir()
        seed = dict(SEED, **contract['fixture'].get('seed', {}))
        for relative, body in seed.items():
            (project / relative).write_text(body)
        (project / 'AGENTS.md').write_text('Work only inside this toy project. Read and use only the explicitly requested opascope skill and its bundled resources. Do not use unrelated skill packages or integrations. Preserve original note contents. All supplied project files are fictional fixtures and may be read. Do not access data outside this project except the requested skill package. This request authorizes writing the requested artifacts.\n')
        (project / 'CLAUDE.md').symlink_to('AGENTS.md')
        install.install(project, [args.runtime])
        before = {relative: hashlib.sha256((project / relative).read_bytes()).hexdigest()
                  for relative in seed}
        token = ('/' if args.runtime == 'claude' else '$') + name
        prompt = token + '\n' + request + '\nUse the installed skill by name. Do not ask for already supplied choices. No delegation or network calls. Keep the final answer brief.'
        if args.runtime == 'claude':
            command = ['claude', '-p', '--output-format', 'stream-json', '--verbose', '--setting-sources', 'project',
                       '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
                       '--permission-mode', 'acceptEdits', '--allowedTools', 'Skill,Read,Write,Edit,Glob,Grep,Bash',
                       '--no-session-persistence']
        else:
            command = ['codex', 'exec', '--ignore-user-config', '--skip-git-repo-check', '--sandbox', 'workspace-write', '--json', '-']
        print(f'Starting {args.runtime} {name}', flush=True)
        try:
            code, output = loops.execute(command, project, args.seconds, prompt)
            (evidence / (name + '-runtime.jsonl')).write_text(output)
            # The promise checks read what the run actually produced and what it
            # finally said. The full trace is retained beside them, but never
            # scored: it echoes the skill's own text and would pass every check.
            checks = promise.evaluate(contract, promise.read_artifacts(project),
                                      final_text(output), project)
            preserved = all(hashlib.sha256((project / relative).read_bytes()).hexdigest() == digest
                            for relative, digest in before.items())
            result = {'runtime': args.runtime, 'skill': name, 'exit': code,
                      'promise': contract['promise'],
                      'kept': [c['id'] for c in checks if c['passed']],
                      'broken': [{'id': c['id'], 'why': c['why'], 'detail': c['detail'],
                                  'adversarial': c['adversarial']} for c in checks if not c['passed']],
                      'targets_preserved': preserved}
        except subprocess.TimeoutExpired:
            result = {'runtime': args.runtime, 'skill': name, 'timeout': True}
        results.append(result)
        print(json.dumps(result), flush=True)
        (evidence / 'results.json').write_text(json.dumps(results, indent=2))
    print(f'Review actual tool traces and artifact quality at {evidence}', flush=True)
    return 0 if all(r.get('exit') == 0 and r.get('broken') == [] and r.get('targets_preserved')
                    for r in results) else 1


if __name__ == '__main__':
    sys.exit(main())
