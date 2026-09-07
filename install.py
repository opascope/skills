#!/usr/bin/env python3
"""Install path-based skills without taking ownership of existing user files."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parent
RECEIPT = '.opascope-skills-install.json'
LOCK = '.opascope-skills-install.lock'
LOCATIONS = {'claude': Path('.claude/skills'), 'codex': Path('.agents/skills')}


def exists(path):
    return path.exists() or path.is_symlink()


def read_receipt(base):
    path = base / RECEIPT
    if not exists(path):
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'Collision: {path}')
    data = json.loads(path.read_text())
    if data.get('package') != 'opascope-skills' or data.get('schema') != 1:
        raise ValueError(f'Unrecognized receipt: {path}')
    for relative in [*data['links'], *data['directories']]:
        p = Path(relative)
        if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0] not in ('.claude', '.agents'):
            raise ValueError('Invalid receipt path')
    return data


@contextmanager
def locked(base):
    lock = base / LOCK
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError(f'Another installation may be running: {lock}. If interrupted, inspect it before removing the lock.')
    os.close(fd)
    try:
        yield
    finally:
        lock.unlink()


def matching_link(path, target):
    return path.is_symlink() and os.readlink(path) == target


def desired_links(base, runtimes):
    links = {}
    for runtime in runtimes:
        for skill in sorted((ROOT / 'skills').glob('opascope*')):
            if not (skill / 'SKILL.md').is_file():
                continue
            for source in sorted(skill.iterdir()):
                if source.name.startswith('.') or source.name == '__pycache__':
                    continue
                destination = LOCATIONS[runtime] / skill.name / source.name
                links[str(destination)] = str(source)
    if not links:
        raise ValueError('No skills found in this checkout')
    return links


def install(base, runtimes):
    base = base.resolve(strict=True)
    with locked(base):
        prior = read_receipt(base)
        if prior and prior['source'] != str(ROOT):
            raise ValueError('This base belongs to a different checkout. Uninstall that checkout first.')
        previous = prior or {'links': {}, 'directories': [], 'runtimes': []}
        runtimes = sorted(set(runtimes) | set(previous['runtimes']))
        desired = desired_links(base, runtimes)
        conflicts = []
        for relative, target in desired.items():
            dest = base / relative
            parent = dest.parent
            while parent != base:
                rel = str(parent.relative_to(base))
                if parent.is_symlink() or (exists(parent) and not parent.is_dir()):
                    conflicts.append(str(parent))
                if parent == dest.parent and exists(parent) and rel not in previous['directories']:
                    conflicts.append(str(parent))
                parent = parent.parent
            if exists(dest) and not (previous['links'].get(relative) == target and matching_link(dest, target)):
                conflicts.append(str(dest))
        if conflicts:
            raise ValueError('Collisions; nothing installed:\n' + '\n'.join(sorted(set(conflicts))))
        created_dirs, created_links = [], []
        try:
            for relative, target in desired.items():
                dest = base / relative
                missing = []
                parent = dest.parent
                while not exists(parent):
                    missing.append(parent)
                    parent = parent.parent
                for directory in reversed(missing):
                    directory.mkdir()
                    created_dirs.append(str(directory.relative_to(base)))
                if not exists(dest):
                    dest.symlink_to(target, target_is_directory=Path(target).is_dir())
                    created_links.append(relative)
            receipt = {
                'package': 'opascope-skills', 'schema': 1, 'source': str(ROOT),
                'runtimes': runtimes,
                'directories': sorted(set(previous['directories'] + created_dirs)),
                'links': {**previous['links'], **desired},
            }
            temporary = base / (RECEIPT + '.' + uuid.uuid4().hex)
            try:
                with temporary.open('x') as stream:
                    json.dump(receipt, stream, indent=2)
                    stream.write('\n')
                temporary.replace(base / RECEIPT)
            finally:
                if temporary.exists():
                    temporary.unlink()
        except BaseException:
            for relative in reversed(created_links):
                dest = base / relative
                if matching_link(dest, desired[relative]):
                    dest.unlink()
            for relative in reversed(created_dirs):
                try:
                    (base / relative).rmdir()
                except OSError:
                    pass
            raise
        # Retired entries are removed only with both ownership and target proof.
        retained = {}
        for relative, target in previous['links'].items():
            if relative in desired:
                continue
            path = base / relative
            parents = list(path.parents)
            parents = parents[:parents.index(base)]
            if any(p.is_symlink() for p in parents):
                retained[relative] = target
                print(f'Preserved retired entry below changed parent: {path}')
            elif matching_link(path, target):
                path.unlink()
            elif exists(path):
                print(f'Preserved changed retired entry: {path}')
                retained[relative] = target
        if set(receipt['links']) != set(desired) | set(retained):
            receipt['links'] = {**desired, **retained}
            temporary = base / (RECEIPT + '.' + uuid.uuid4().hex)
            with temporary.open('x') as stream:
                json.dump(receipt, stream, indent=2)
                stream.write('\n')
            temporary.replace(base / RECEIPT)
    print(f'Installed {len(desired)} links for {", ".join(runtimes)} in {base}')
    print('Open a new session. Claude Code: /opascope | Codex: $opascope')
    print('Try: define done for sorting a folder of notes without losing any.')
    print(f'Uninstall: python3 "{ROOT / "install.py"}" uninstall --base "{base}" --yes')


def uninstall(base):
    base = base.resolve(strict=True)
    with locked(base):
        receipt = read_receipt(base)
        if not receipt:
            print('Nothing installed here.')
            return
        preserved = []
        for relative, target in receipt['links'].items():
            path = base / relative
            # Refuse traversal through a parent replaced since installation.
            parents = list(path.parents)
            parents = parents[:parents.index(base)]
            if any(p.is_symlink() for p in parents):
                preserved.append(relative)
            elif matching_link(path, target):
                path.unlink()
            elif exists(path):
                preserved.append(relative)
        for relative in sorted(receipt['directories'], key=lambda s: len(Path(s).parts), reverse=True):
            path = base / relative
            parents = list(path.parents)
            parents = parents[:parents.index(base)]
            if path.is_symlink() or any(p.is_symlink() for p in parents):
                preserved.append(relative)
                continue
            try:
                path.rmdir()
            except FileNotFoundError:
                pass
            except OSError:
                preserved.append(relative)
        (base / RECEIPT).unlink()
    print('Removed installed links, receipt and empty directories created by the installer.')
    print('Source checkout and task artifacts retained.')
    if preserved:
        print('Preserved user changes or nonempty directories:\n' + '\n'.join(sorted(set(preserved))))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install', 'uninstall', 'status'], nargs='?', default='install')
    parser.add_argument('--runtime', choices=['claude', 'codex', 'both'])
    parser.add_argument('--base', type=Path, help='Home directory for global install, or project directory for local install')
    parser.add_argument('--yes', action='store_true', help='Use explicit flags or defaults without prompts')
    args = parser.parse_args(argv)
    base = args.base
    runtime = args.runtime
    if not args.yes and args.action != 'status':
        print('Opascope Skills | six work skills, one router, no build step')
        print('Installation creates links back to this checkout. Keep the checkout in place.')
        try:
            if args.action == 'install' and runtime is None:
                value = input('Runtime: [1] Both  [2] Claude Code  [3] Codex CLI (1): ').strip() or '1'
                if value not in ('1', '2', '3'):
                    raise ValueError('Choose 1, 2 or 3')
                runtime = {'1': 'both', '2': 'claude', '3': 'codex'}[value]
            if base is None:
                value = input('Location: [1] Your home, all projects  [2] Current project (1): ').strip() or '1'
                if value not in ('1', '2'):
                    raise ValueError('Choose 1 or 2')
                base = Path.home() if value == '1' else Path.cwd()
            print(f'{args.action.capitalize()} at {base.expanduser().resolve()}')
            if input('Continue? [Y/n]: ').strip().lower() not in ('', 'y', 'yes'):
                print('Cancelled. Nothing changed.')
                return 0
        except EOFError:
            raise ValueError('No interactive input. Re-run with --yes and optional --runtime/--base.')
    base = (base or Path.home()).expanduser()
    if args.action == 'install':
        install(base, ['claude', 'codex'] if (runtime or 'both') == 'both' else [runtime])
    elif args.action == 'uninstall':
        uninstall(base)
    else:
        print(json.dumps(read_receipt(base) or {'installed': False}, indent=2))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)
