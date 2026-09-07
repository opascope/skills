"""Foreground, bounded runtime adapters with separately executed completion checks."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

IMMUTABLE = ('task.json', 'loop.json', 'CONTEXT.md', 'SAFETY.md', 'init.md')


def contract(task):
    data = json.loads((task / 'loop.json').read_text())
    if data.get('schema') != 1 or not isinstance(data.get('objective'), str) or not data['objective'].strip():
        raise ValueError('Loop requires schema 1 and a nonempty objective')
    items = data.get('items', [])
    if not items or not isinstance(items, list):
        raise ValueError('A loop needs at least one item')
    ids = set()
    for item in items:
        if not isinstance(item.get('id'), str) or item['id'] in ids or not item['id']:
            raise ValueError('Item IDs must be nonempty and unique')
        if not isinstance(item.get('description'), str) or not item['description'].strip():
            raise ValueError('Each item needs a description')
        if not isinstance(item.get('depends', []), list) or any(dep not in ids for dep in item.get('depends', [])):
            raise ValueError('Dependencies must precede the item in the contract')
        ids.add(item['id'])
    for proof in [*[i.get('proof') for i in items], data.get('final_proof')]:
        if not isinstance(proof, list) or not proof or not all(isinstance(s, str) and s for s in proof):
            raise ValueError('Each proof and final_proof must be a nonempty argument array; exit 0 means pass')
    for name in IMMUTABLE:
        p = task / name
        if not p.is_file() or p.is_symlink() or not p.read_text().strip():
            raise ValueError(f'Missing nonempty plain file: {name}')
    for name in ('CHECKLIST.md', 'NOTES.md', 'BLOCKERS.md'):
        if not (task / name).is_file() or (task / name).is_symlink():
            raise ValueError(f'Missing plain file: {name}')
    for key in ('verifier_files', 'outputs'):
        if not isinstance(data.get(key, []), list) or not all(isinstance(s, str) and s for s in data.get(key, [])):
            raise ValueError(f'{key} must be an array of paths')
    return data


def fingerprint(task, data):
    paths = [task / name for name in IMMUTABLE]
    project = Path(json.loads((task / 'task.json').read_text())['project'])
    paths += [project / p for p in data.get('verifier_files', [])]
    values = {}
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Verifier/contract is missing or not a plain file: {path}')
        values[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return values


def seal(task):
    data = contract(task)
    result = fingerprint(task, data)
    target = task / 'seal.json'
    if target.exists():
        if json.loads(target.read_text()) != result:
            raise ValueError('Sealed criteria changed. Create a new task with the reviewed replacement contract.')
        print('Seal unchanged.')
        return
    with target.open('x') as stream:
        json.dump(result, stream, indent=2)
    print('Sealed criteria and verifier files. Review loop.json before authorizing run.')


def execute(argv, cwd, seconds, prompt=None):
    process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE if prompt is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               start_new_session=True)
    try:
        output, _ = process.communicate(prompt, timeout=seconds)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        raise
    return process.returncode, output


def runtime_command(runtime, task=None):
    extra = ['--add-dir', str(task)] if task else []
    if runtime == 'codex':
        return ['codex', 'exec', '--skip-git-repo-check', '--sandbox', 'workspace-write', *extra, '-']
    return ['claude', '-p', '--permission-mode', 'acceptEdits', '--allowedTools', 'Read,Write,Edit,Bash', *extra]


def run(task, runtime, steps, seconds):
    data = contract(task)
    expected = json.loads((task / 'seal.json').read_text())
    if fingerprint(task, data) != expected:
        raise ValueError('Criteria differ from the seal; no runtime started')
    base = Path(json.loads((task / 'task.json').read_text())['project'])
    lock = task / 'run.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError('Loop is already running or was interrupted. Inspect run.lock before removing it.')
    os.close(fd)
    from kit import save

    def unchanged():
        if fingerprint(task, data) != expected or json.loads((task / 'seal.json').read_text()) != expected:
            raise ValueError('Criteria changed during execution; stopping without success')

    def proofs():
        results = []
        for index, proof in enumerate([i['proof'] for i in data['items']] + [data['final_proof']]):
            unchanged()
            code, output = execute(proof, base, min(seconds, 60))
            unchanged()
            results.append(code == 0)
            save(task, 'proof', f'Check {index + 1}\nExit: {code}\n\n{output}')
            print(f'Proof {index + 1}: exit {code}', flush=True)
        return results

    def blocked():
        return any(line.startswith('- BLOCKED:') for line in (task / 'BLOCKERS.md').read_text().splitlines())

    try:
        last = None
        stalled = 0
        for iteration in range(steps + 1):
            unchanged()
            if blocked():
                print('BLOCKED: read BLOCKERS.md; no success claimed.')
                return 2
            results = proofs()
            if all(results):
                save(task, 'completion', f'Objective: {data["objective"]}\nAll {len(results)} independent proof commands exited 0.')
                print('DONE: every item and final proof passed.')
                return 0
            if iteration == steps:
                print('PAUSED: iteration cap reached. Artifacts preserved; rerun explicitly to resume.')
                return 3
            progress = repr(results)
            for name in data.get('outputs', []):
                p = base / name
                progress += hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else 'missing'
            if progress == last:
                stalled += 1
            else:
                stalled = 0
            last = progress
            if stalled >= 3:
                save(task, 'pause', 'No proof or declared output changed in three consecutive runtime calls.')
                print('PAUSED: no measurable progress in three calls.')
                return 3
            prompt = (
                f'Work only on the authorized task in {task}. Read the project instructions, then '
                f'{task}/init.md, CONTEXT.md, SAFETY.md, loop.json, CHECKLIST.md, NOTES.md, BLOCKERS.md '
                'from that task directory. Resume the checkpoint and make one bounded increment. '
                'Do not change loop.json, seal.json, init.md, CONTEXT.md, SAFETY.md or verifier files. '
                'Run item checks before updating checklist status. Update NOTES.md after meaningful '
                'substeps. If blocked or missing permission, append a line beginning "- BLOCKED:" '
                'to BLOCKERS.md and stop. Do not launch another loop runner. The parent independently '
                'checks proofs. Preserve all unrelated files.\n'
                f'Current independent proof results: {results}\n'
            )
            print(f'Runtime call {iteration + 1}/{steps}: {runtime}', flush=True)
            code, output = execute(runtime_command(runtime, task), base, seconds, prompt)
            save(task, 'runtime', f'Runtime: {runtime}\nExit: {code}\n\n{output}')
            unchanged()
            if code:
                print(f'BLOCKED: runtime exited {code}; see the latest runtime artifact.')
                return 2
        return 3
    except subprocess.TimeoutExpired:
        save(task, 'pause', 'A command exceeded its time budget. Its process group was stopped. Resume from the checkpoint.')
        print('PAUSED: command timeout; progress preserved.')
        return 3
    except KeyboardInterrupt:
        save(task, 'pause', 'Interrupted by the user. Resume from NOTES.md and recheck filesystem state.')
        return 130
    finally:
        lock.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['validate', 'seal', 'run'])
    parser.add_argument('task', type=Path, help='Task directory created by kit.py new')
    parser.add_argument('--runtime', choices=['claude', 'codex'])
    parser.add_argument('--steps', type=int, default=10)
    parser.add_argument('--seconds', type=int, default=300, help='Maximum seconds per runtime call; proofs capped at 60')
    args = parser.parse_args(argv)
    if not 1 <= args.steps <= 100 or not 1 <= args.seconds <= 3600:
        raise ValueError('steps must be 1..100 and seconds 1..3600')
    task = args.task.expanduser().resolve(strict=True)
    if args.action == 'validate':
        data = contract(task)
        fingerprint(task, data)
        print(f'Valid contract: {len(data["items"])} items. Proof behavior is checked on run.')
        return 0
    if args.action == 'seal':
        seal(task)
        return 0
    if not args.runtime:
        raise ValueError('run requires --runtime claude or --runtime codex')
    return run(task, args.runtime, args.steps, args.seconds)
