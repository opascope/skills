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

CASES = {
    'define-done': 'Define done for sorting the two supplied notes by topic without losing their contents. Alignment only. Save the objective.',
    'interrogate': 'Light interrogation before sorting the two supplied notes. The output is a topic index for my own reading, named index.md. Preserve every original byte. No deadline, no external actions. Use these choices as final and save the brief; do not implement it.',
    'planning': 'Plan a topic index for the two supplied notes without changing the originals. Output index.md with one link per note. Planning only, use sequential self-review. Save the plan.',
    'optimize': 'Audit only the two supplied notes and duplicated-index.txt for deletion or compression. Search this project for consumers. Produce the recommendation report and preserve every target byte. Scope is this toy project only.',
    'session-handoff': 'Write a handoff for this toy task: notes are present, index creation is not started, next step is inspect both notes and plan a topic index. No blockers or external actions. Verify disk state. Save and prove cold pickup.',
    'loop-builder': 'Build and validate a one-item loop that creates greeting.txt containing exactly hello and a newline, preserving both notes. Use an inline standard-library Python proof for the exact content, and a final proof for greeting plus original note hashes. Seal the loop, but do not run it. There are no external actions. Save every required loop artifact.',
    'router': 'Which one of your work-process skills fits this request: I need a falsifiable completion sentence for organizing notes? Recommend only; do not execute the chosen skill.',
}
KINDS = {'define-done': 'objective', 'interrogate': 'brief', 'planning': 'plan',
         'optimize': 'optimization', 'session-handoff': 'handoff'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', choices=['claude', 'codex'], required=True)
    parser.add_argument('--only', choices=list(CASES))
    parser.add_argument('--seconds', type=int, default=300)
    args = parser.parse_args()
    evidence = kit.new_task(ROOT, 'live-' + args.runtime)
    print(f'Evidence: {evidence}', flush=True)
    results = []
    for name, request in CASES.items():
        if args.only and args.only != name:
            continue
        project = Path(tempfile.mkdtemp(prefix='opascope-live-' + args.runtime + '-' + name + '-'))
        (evidence / (name + '-project.txt')).write_text(str(project) + '\n')
        (project / 'notes').mkdir()
        (project / 'notes/alpha.txt').write_text('Topic: trees\nLeaves shade the path.\n')
        (project / 'notes/beta.txt').write_text('Topic: water\nRain fills the pond.\n')
        (project / 'duplicated-index.txt').write_text('notes/alpha.txt\nnotes/alpha.txt\nnotes/beta.txt\n')
        (project / 'AGENTS.md').write_text('Work only inside this toy project. Read and use only the explicitly requested opascope skill and its bundled resources. Do not use unrelated skill packages or integrations. Preserve original note contents. All supplied project files are fictional fixtures and may be read. Do not access data outside this project except the requested skill package. This request authorizes writing the requested artifacts.\n')
        (project / 'CLAUDE.md').symlink_to('AGENTS.md')
        install.install(project, [args.runtime])
        before = {str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest() for p in (project / 'notes').iterdir()}
        before['duplicated-index.txt'] = hashlib.sha256((project / 'duplicated-index.txt').read_bytes()).hexdigest()
        skill = 'opascope' if name == 'router' else 'opascope-' + name
        token = ('/' if args.runtime == 'claude' else '$') + skill
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
            artifacts = list((project / '.opascope-work').glob('*/*'))
            if name in KINDS:
                completed = any(p.name.startswith(KINDS[name] + '-') and p.suffix == '.md' for p in artifacts)
            elif name == 'loop-builder':
                completed = any(p.name == 'seal.json' for p in artifacts)
            else:
                completed = 'opascope-define-done' in output
            preserved = all(hashlib.sha256((project / p).read_bytes()).hexdigest() == digest for p, digest in before.items())
            # The actual tool trace is retained for manual review of skill loading and content.
            result = {'runtime': args.runtime, 'skill': name, 'exit': code,
                      'expected_artifact': completed, 'targets_preserved': preserved}
        except subprocess.TimeoutExpired:
            result = {'runtime': args.runtime, 'skill': name, 'timeout': True}
        results.append(result)
        print(json.dumps(result), flush=True)
        (evidence / 'results.json').write_text(json.dumps(results, indent=2))
    print(f'Review actual tool traces and artifact quality at {evidence}', flush=True)
    return 0 if all(r.get('exit') == 0 and r.get('expected_artifact') and r.get('targets_preserved') for r in results) else 1


if __name__ == '__main__':
    sys.exit(main())
