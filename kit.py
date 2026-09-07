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


def update(check=False, offline=False, bases=()):
    local = (ROOT / 'VERSION').read_text().strip()
    if offline:
        result = git('tag', '--list', 'v*', check=False)
    else:
        result = git('ls-remote', '--tags', '--refs', 'origin', check=False)
    if result.returncode:
        if not offline:
            raise ValueError('Version lookup unavailable. Check the remote and your Git authentication.')
        return
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
    if not bases:
        receipt = install.read_receipt(Path.home())
        if receipt and receipt['source'] == str(ROOT):
            bases = [Path.home()]
    for base in bases:
        receipt = install.read_receipt(Path(base))
        if not receipt or receipt['source'] != str(ROOT):
            raise ValueError(f'No matching installation receipt at {base}')
    git('fetch', '--no-tags', 'origin', f'refs/tags/{tag}:refs/tags/{tag}')
    if git('merge-base', '--is-ancestor', 'HEAD', tag, check=False).returncode:
        raise ValueError('Release diverges from this checkout; refusing to overwrite local history.')
    git('merge', '--ff-only', tag)
    for base in bases:
        # Run the updated implementation, not the module loaded before the merge.
        receipt = install.read_receipt(Path(base))
        runtimes = receipt['runtimes']
        runtime = 'both' if len(runtimes) == 2 else runtimes[0]
        subprocess.run([sys.executable, str(ROOT / 'install.py'), '--base', str(base), '--runtime', runtime, '--yes'], check=True)
    print(f'Updated to {tag}. Existing links follow the checkout. Re-run install.py for any other installation bases.')


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
