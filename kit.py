#!/usr/bin/env python3
"""Portable artifacts, local usage ranking, versioned updates and foreground loops."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'lib'))
CONFIG = '.opascope-skills.json'
DEFAULT_ARTIFACTS = '.opascope-work'


def timestamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')


def project(path):
    current = Path(path).expanduser().resolve(strict=True)
    if not current.is_dir():
        raise ValueError('Project must be a directory')
    for parent in [current, *current.parents]:
        if any((parent / name).exists() for name in (CONFIG, DEFAULT_ARTIFACTS, '.git')):
            return parent
    return current


def artifact_root(path, create=False):
    base = project(path)
    config_path = base / CONFIG
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    if not isinstance(config, dict) or set(config) - {'artifact_dir'}:
        raise ValueError('Only the artifact_dir configuration key is supported')
    value = config.get('artifact_dir', DEFAULT_ARTIFACTS)
    if not isinstance(value, str) or not value.strip():
        raise ValueError('artifact_dir must be a nonempty path')
    root = (base / Path(value).expanduser()).resolve()
    if root == base or root in base.parents:
        raise ValueError('Artifact directory must not be the project root or an ancestor')
    marker = root / '.project.json'
    if root.exists():
        if not marker.is_file() or marker.is_symlink():
            raise ValueError(f'Unowned artifact directory: {root}')
        owner = json.loads(marker.read_text()).get('project')
        if not isinstance(owner, str) or (root / owner).resolve() != base:
            raise ValueError('Artifact directory belongs to another project; choose a unique artifact_dir')
    elif create:
        root.mkdir(parents=True)
        with marker.open('x') as stream:
            json.dump({'project': os.path.relpath(base, root), 'schema': 1}, stream)
    return base, root


def new_task(path, title):
    base, root = artifact_root(path, create=True)
    slug = re.sub('[^a-z0-9]+', '-', title.lower()).strip('-')[:48] or 'task'
    task = root / (slug + '-' + uuid.uuid4().hex[:10])
    task.mkdir()
    (task / 'task.json').write_text(json.dumps({'title': title, 'created': timestamp(), 'project': os.path.relpath(base, task)}, indent=2) + '\n')
    return task


def resolve_task(path, task_id):
    _, root = artifact_root(path)
    if not re.fullmatch('[a-z0-9][a-z0-9-]*', task_id):
        raise ValueError('Use the task ID printed by new or resume')
    task = root / task_id
    if task.is_symlink() or not (task / 'task.json').is_file():
        raise ValueError(f'Unknown task: {task_id}')
    return task


def save(task, kind, content):
    if not re.fullmatch('[a-z][a-z0-9-]*', kind) or not content.strip():
        raise ValueError('Artifact kind must be a slug and content must be nonempty')
    target = task / f'{kind}-{timestamp()}-{uuid.uuid4().hex[:8]}.md'
    with target.open('x') as stream:
        stream.write(content.rstrip() + '\n')
    return target


def resume(path, task_id=None):
    _, root = artifact_root(path)
    if task_id:
        task = resolve_task(path, task_id)
        handoffs = sorted(task.glob('handoff-*.md'))
        if handoffs:
            print(f'Handoff: {handoffs[-1]}\n\n{handoffs[-1].read_text()}')
        else:
            print(f'No handoff yet. Read artifacts in {task}')
        return
    tasks = sorted(root.glob('*/task.json'), key=lambda p: p.parent.name)
    if not tasks:
        print('No tasks yet. Start with: kit.py new "task title"')
    for identity in tasks:
        data = json.loads(identity.read_text())
        handoffs = sorted(identity.parent.glob('handoff-*.md'))
        print(f'{identity.parent.name}: {data["title"]} | handoff: {handoffs[-1].name if handoffs else "none"}')


def git(*args, check=True):
    return subprocess.run(['git', '-C', str(ROOT), *args], text=True, capture_output=True, check=check, timeout=30)


def versions(text):
    return sorted({tuple(map(int, m)) for m in re.findall(r'(?:^|\s)(?:refs/tags/)?v(\d+)\.(\d+)\.(\d+)(?=\s|$)', text)})


def local_versions():
    """Release tags already in the package checkout, or None when Git cannot list them."""
    try:
        result = git('tag', '--list', 'v*', check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return None if result.returncode else versions(result.stdout)


ARTIFACT = re.compile(r'([a-z][a-z0-9-]*)-(\d{8}T\d{12}Z)-[0-9a-f]{8}\.md')


def artifacts(task):
    """Saved artifacts of one task, oldest first, as (kind, path) pairs."""
    found = [(m.group(2), m.group(1), p) for p in task.iterdir() for m in [ARTIFACT.fullmatch(p.name)] if m and p.is_file()]
    return [(kind, p) for _, kind, p in sorted(found)]


def next_step(task, saved):
    kinds = {kind for kind, _ in saved if kind != 'optimization'}
    notable = [kind for kind, _ in saved if kind != 'optimization']
    if notable and notable[-1] == 'handoff':
        return 'follow the latest handoff'
    if (task / 'seal.json').exists():
        return 'continue the sealed loop: kit.py loop run'
    for kind, step in (('plan', 'do the work, or loop-builder to run it unattended'),
                       ('objective', 'planning'), ('brief', 'define-done')):
        if kind in kinds:
            return step
    return 'interrogate'


def start(path, task_id=None):
    """Report version, project, tasks and the next step. Reads only."""
    _, root = artifact_root(path)
    if task_id:
        task = resolve_task(path, task_id)
    local = (ROOT / 'VERSION').read_text().strip()
    available = local_versions()
    if available is None:
        print(f'PACKAGE: {local} (update check unavailable)')
    else:
        current = tuple(map(int, local.split('.')))
        newer = [v for v in available if v > current]
        if newer:
            print(f'PACKAGE: {local}, v{".".join(map(str, newer[-1]))} available (update only when the user asks)')
        else:
            print(f'PACKAGE: {local} (up to date)')
    if not root.exists():
        print('PROJECT: new\nNEXT: interrogate')
        return
    tasks = sorted((p.parent for p in root.glob('*/task.json') if not p.parent.is_symlink()), key=lambda p: p.name)
    print(f'PROJECT: known, {len(tasks)} task{"" if len(tasks) == 1 else "s"}')
    if task_id:
        saved = artifacts(task)
        handoffs = [p for kind, p in saved if kind == 'handoff']
        print(f'TASK: {task.name} "{json.loads((task / "task.json").read_text())["title"]}"')
        for _, p in saved:
            print(f'SAVED: {p}')
        print(f'HANDOFF: {handoffs[-1] if handoffs else "none"}')
        print(f'NEXT: {next_step(task, saved)}')
        return
    if not tasks:
        print('NEXT: interrogate')
    for task in tasks:
        saved = artifacts(task)
        kinds = list(dict.fromkeys(kind for kind, _ in saved))
        handoffs = [p for kind, p in saved if kind == 'handoff']
        title = json.loads((task / 'task.json').read_text())['title']
        print(f'TASK: {task.name} "{title}" | saved: {", ".join(kinds) or "none"} | handoff: {handoffs[-1].name if handoffs else "none"} | next: {next_step(task, saved)}')


def whats_new(old, new):
    """CHANGELOG sections newer than old and no newer than new, newest first, verbatim."""
    path = ROOT / 'CHANGELOG.md'
    if not path.is_file():
        return []
    sections, current = [], None
    for line in path.read_text().splitlines():
        match = re.fullmatch(r'## (\d+)\.(\d+)\.(\d+)\b.*', line)
        if match or line.startswith('## ') or line.startswith('# '):
            current = [tuple(map(int, match.groups())), [line]] if match else None
            if current:
                sections.append(current)
        elif current:
            current[1].append(line)
    chosen = [s for s in sections if old < s[0] <= new]
    return ['\n'.join(lines).strip() for _, lines in sorted(chosen, key=lambda s: s[0], reverse=True)]


def update(check=False, offline=False, bases=()):
    local = (ROOT / 'VERSION').read_text().strip()
    if offline:
        available = local_versions()
        if available is None:
            return
    else:
        result = git('ls-remote', '--tags', '--refs', 'origin', check=False)
        if result.returncode:
            raise ValueError('Version lookup unavailable. Check the remote and your Git authentication.')
        available = versions(result.stdout)
    current = tuple(map(int, local.split('.')))
    latest = available[-1] if available else current
    if latest <= current:
        if not offline:
            print(f'Installed {local}; no newer stable release found.')
        return
    tag = 'v' + '.'.join(map(str, latest))
    print(f'Update available: {local} -> {tag}. Read CHANGELOG.md before upgrading.')
    if check or offline:
        return
    if git('status', '--porcelain').stdout.strip():
        raise ValueError('Checkout has local changes. Commit or move them before updating; nothing was stashed.')
    branch = git('branch', '--show-current').stdout.strip()
    if branch != 'main':
        raise ValueError('Update requires the main branch. Feature and detached checkouts are not changed.')
    import install
    # Hold the checkout for the whole update, so no install starts or ends unseen.
    with install.checkout_locked(ROOT):
        named = bool(bases)
        if not bases:
            # Refuse before the checkout moves: an unreadable list would leave installs stale.
            install.read_index(ROOT)
            found, stale = install.installed_bases(ROOT)
            if stale:
                raise ValueError('Listed installations were not found, so nothing was updated:\n' + '\n'.join(stale) +
                                 '\nReconnect them, or forget one you removed with: python3 install.py forget --base <path>')
            bases = [Path(base) for base, _ in found]
        for base in bases:
            receipt = install.read_receipt(Path(base))
            if not receipt or receipt['source'] != str(ROOT):
                raise ValueError(f'No matching installation receipt at {base}')
        git('fetch', '--no-tags', 'origin', f'refs/tags/{tag}:refs/tags/{tag}')
        if git('merge-base', '--is-ancestor', 'HEAD', tag, check=False).returncode:
            raise ValueError('Release diverges from this checkout; refusing to overwrite local history.')
        git('merge', '--ff-only', tag)
        failed = []
        env = dict(os.environ, OPASCOPE_CHECKOUT_LOCKED=str(install.index_path(ROOT).with_name(install.INDEX + '.lock')))
        for base in bases:
            # Run the updated implementation, not the module loaded before the merge.
            # One base failing must not leave the others stale: the checkout has already moved.
            receipt = install.read_receipt(Path(base))
            runtimes = receipt['runtimes']
            runtime = 'both' if len(runtimes) == 2 else runtimes[0]
            result = subprocess.run([sys.executable, str(ROOT / 'install.py'), '--base', str(base), '--runtime', runtime, '--yes'], env=env)
            if result.returncode:
                failed.append((str(base), runtime))
        if failed:
            print(f'Updated to {tag}, but {len(failed)} installation(s) were not refreshed.')
        elif named:
            print(f'Updated to {tag}. Refreshed the installations you named. Run install.py status to see the others.')
        else:
            print(f'Updated to {tag}. Refreshed every installation this checkout knows about.')
        notes = whats_new(current, latest)
        if notes:
            print("What's new:\n\n" + '\n\n'.join(notes))
        if failed:
            raise ValueError('Not refreshed. Fix the error above, then run for each:\n' + '\n'.join(
                f'python3 "{ROOT / "install.py"}" --base "{base}" --runtime {runtime} --yes' for base, runtime in failed))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('new', 'resume', 'path', 'config', 'save'):
        p = sub.add_parser(name)
        p.add_argument('--project', type=Path, default=Path.cwd())
        if name == 'new':
            p.add_argument('title')
        if name == 'resume':
            p.add_argument('task', nargs='?')
        if name == 'save':
            p.add_argument('task')
            p.add_argument('kind')
        if name == 'config':
            p.add_argument('--artifact-dir', help='Set the single project override; existing config is preserved on collision')
    p = sub.add_parser('start', help='Report version, project, tasks and the next step; reads only')
    p.add_argument('--project', type=Path, default=Path.cwd())
    p.add_argument('--task')
    p = sub.add_parser('update')
    p.add_argument('--check', action='store_true')
    p.add_argument('--offline', action='store_true')
    p.add_argument('--base', type=Path, action='append', default=[], help='Installation base to relink after update; repeat as needed')
    p = sub.add_parser('usage')
    p.add_argument('paths', type=Path, nargs='*')
    p.add_argument('--json', action='store_true')
    p.add_argument('--skill', action='append', default=[], help='Also recognize this historical skill name; repeat as needed')
    p = sub.add_parser('loop')
    p.add_argument('args', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command == 'new':
        print(new_task(args.project, args.title))
    elif args.command == 'path':
        print(artifact_root(args.project)[1])
    elif args.command == 'resume':
        resume(args.project, args.task)
    elif args.command == 'save':
        print(save(resolve_task(args.project, args.task), args.kind, sys.stdin.read()))
    elif args.command == 'config':
        base = project(args.project)
        if args.artifact_dir:
            with (base / CONFIG).open('x') as stream:
                json.dump({'artifact_dir': args.artifact_dir}, stream, indent=2)
                stream.write('\n')
        print((base / CONFIG).read_text() if (base / CONFIG).exists() else json.dumps({'artifact_dir': DEFAULT_ARTIFACTS}))
    elif args.command == 'start':
        start(args.project, args.task)
    elif args.command == 'update':
        update(args.check, args.offline, args.base)
    elif args.command == 'usage':
        import measurement
        measurement.report(args.paths, args.json, args.skill)
    elif args.command == 'loop':
        import loops
        return loops.main(args.args)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)
