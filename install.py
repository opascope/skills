#!/usr/bin/env python3
"""Install path-based skills without taking ownership of existing user files."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parent
RECEIPT = '.opascope-skills-install.json'
LOCK = '.opascope-skills-install.lock'
INDEX = '.opascope-skills-installs.json'
LOCATIONS = {'claude': Path('.claude/skills'), 'codex': Path('.agents/skills')}
# Every home folder a runtime also reads skills from. A project install cannot use
# a short name one of these already gives to another skill: the runtime would load that one.
HOME_LOCATIONS = {'claude': [Path('.claude/skills')], 'codex': [Path('.agents/skills'), Path('.codex/skills')]}


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


def index_path(root=None):
    """Where a checkout lists the bases it is installed in. The receipts stay the proof."""
    return (root or ROOT) / INDEX


def read_index(root=None):
    """The listed bases. Refuses a file this package did not write, so it is never replaced."""
    path = index_path(root)
    if not exists(path):
        return []
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError
        data = json.loads(path.read_text())
        if data.get('package') != 'opascope-skills' or data.get('schema') != 1 or \
                not isinstance(data.get('bases'), list) or \
                not all(isinstance(b, str) for b in data['bases']):
            raise ValueError
    except (OSError, ValueError, AttributeError):
        raise ValueError(f'Unrecognized install list, left unchanged: {path}')
    return data['bases']


def write_index(bases, root=None):
    path = index_path(root)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex)
    try:
        with temporary.open('x') as stream:
            json.dump({'package': 'opascope-skills', 'schema': 1, 'bases': sorted(set(bases))}, stream, indent=2)
            stream.write('\n')
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


_HELD = threading.local()


@contextmanager
def checkout_locked(root=None):
    """One install, uninstall or update at a time per checkout, for the whole operation.

    Reentrant in this process, and inherited by installers an update starts.
    A checkout that cannot hold a lock file cannot hold the list either, so it
    proceeds unlocked and the list stays as it is.
    """
    lock = index_path(root).with_name(INDEX + '.lock')
    held = _HELD.__dict__.setdefault('locks', [])
    if str(lock) in held or os.environ.get('OPASCOPE_CHECKOUT_LOCKED') == str(lock):
        yield
        return
    deadline = time.monotonic() + 10
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                raise ValueError(f'Another install, uninstall or update is running: {lock}. '
                                 'If interrupted, inspect it before removing the lock.')
            time.sleep(0.05)
        except OSError:
            yield
            return
    os.close(fd)
    held.append(str(lock))
    try:
        yield
    finally:
        held.remove(str(lock))
        lock.unlink()


def change_index(add=(), remove=(), root=None):
    with checkout_locked(root):
        bases = [b for b in read_index(root) if b not in remove]
        write_index(bases + list(add), root)


def record(add=(), remove=()):
    """Update the list after a base changed. The base is already done, so never fail it."""
    try:
        change_index(add=add, remove=remove)
    except (OSError, ValueError) as exc:
        print(f'Note: could not update the install list ({exc}). '
              'status still finds installs in your home or current folder.')


def installed_bases(root=None):
    """Bases holding a receipt from this checkout, and listed bases that no longer do.

    Home and the current directory are also checked, so installs made before the
    list existed are found and added to it.
    """
    root = root or ROOT
    try:
        listed, usable = read_index(root), True
    except ValueError:
        listed, usable = [], False
    found, stale, seen = [], [], set()
    for candidate in listed + [str(Path.home()), str(Path.cwd())]:
        # Compare resolved paths, so a symlinked home is not counted twice.
        try:
            base = Path(candidate).resolve()
        except (OSError, RuntimeError):
            continue
        if str(base) in seen:
            continue
        seen.add(str(base))
        try:
            receipt = read_receipt(base)
        except (OSError, ValueError, KeyError):
            receipt = None
        if receipt and receipt.get('source') == str(root):
            found.append((str(base), receipt))
        elif candidate in listed:
            stale.append(candidate)
    if usable and any(base not in listed for base, _ in found):
        try:
            with checkout_locked(root):
                # Check again under the lock, so a base uninstalled meanwhile is not re-added.
                add = [base for base, _ in found if base not in listed and
                       (read_receipt(Path(base)) or {}).get('source') == str(root)]
                change_index(add=add, root=root)
        except (OSError, ValueError, KeyError):
            pass  # Reporting what exists matters more than recording it.
    return found, stale


def skill_dirs(root):
    """A skill is a top-level folder that holds a SKILL.md."""
    return sorted(p for p in Path(root).iterdir()
                  if p.is_dir() and not p.name.startswith('.') and (p / 'SKILL.md').is_file())


def matching_link(path, target):
    return path.is_symlink() and os.readlink(path) == target


def long_name(name):
    return name if name == 'opascope' else 'opascope-' + name


def shadowed(name, runtimes, base):
    """True when a home skill folder the runtime also reads gives this name to another skill."""
    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        return False
    if home == base:
        return False
    for runtime in runtimes:
        for location in HOME_LOCATIONS[runtime]:
            path = home / location / name
            if not exists(path):
                continue
            try:
                if ROOT in (path / 'SKILL.md').resolve(strict=True).parents:
                    continue  # This package's own home install.
            except (OSError, RuntimeError):
                pass
            return True
    return False


def install_names(base, runtimes, owned_directories):
    """The name each skill installs under: its short name, or its long name when
    something this package does not own already holds the short one, here or in
    a home skill folder the runtime also reads."""
    names = {}
    for skill in skill_dirs(ROOT):
        short = skill.name
        taken = any(exists(base / LOCATIONS[r] / short) and
                    str(LOCATIONS[r] / short) not in owned_directories for r in runtimes)
        if short != 'opascope' and not taken:
            taken = shadowed(short, runtimes, base)
        names[short] = long_name(short) if taken else short
    return names


def desired_links(base, runtimes, names=None):
    links = {}
    for runtime in runtimes:
        for skill in skill_dirs(ROOT):
            name = (names or {}).get(skill.name, skill.name)
            for source in sorted(skill.iterdir()):
                if source.name.startswith('.') or source.name == '__pycache__':
                    continue
                destination = LOCATIONS[runtime] / name / source.name
                links[str(destination)] = str(source)
    if not links:
        raise ValueError('No skills found in this checkout')
    return links


def install(base, runtimes):
    base = base.resolve(strict=True)
    if base == ROOT or ROOT in base.parents:
        raise ValueError(f'Refusing to install inside the package checkout: {base}. '
                         'It would block updates. Choose your home or another project.')
    with checkout_locked(), locked(base):
        prior = read_receipt(base)
        if prior and prior['source'] != str(ROOT):
            raise ValueError('This base belongs to a different checkout. Uninstall that checkout first.')
        previous = prior or {'links': {}, 'directories': [], 'runtimes': []}
        runtimes = sorted(set(runtimes) | set(previous['runtimes']))
        names = install_names(base, runtimes, previous['directories'])
        desired = desired_links(base, runtimes, names)
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
            # A link the receipt recorded and that still points where it recorded is owned.
            owned = relative in previous['links'] and matching_link(dest, previous['links'][relative])
            if exists(dest) and not owned:
                conflicts.append(str(dest))
        if conflicts:
            raise ValueError('Collisions; nothing installed:\n' + '\n'.join(sorted(set(conflicts))))
        created_dirs, created_links, retargeted = [], [], {}
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
                if dest.is_symlink() and os.readlink(dest) != target and relative in previous['links']:
                    retargeted[relative] = os.readlink(dest)
                    dest.unlink()
                if not exists(dest):
                    dest.symlink_to(target, target_is_directory=Path(target).is_dir())
                    created_links.append(relative)
            receipt = {
                'package': 'opascope-skills', 'schema': 1, 'source': str(ROOT),
                'runtimes': runtimes,
                'installed_as': names,
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
            for relative, old_target in retargeted.items():
                dest = base / relative
                if not exists(dest):
                    dest.symlink_to(old_target, target_is_directory=Path(old_target).is_dir())
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
        # Owned skill directories that retired links left empty go too, so an
        # upgrade from the long names leaves no empty folder behind.
        needed = {str(Path(r).parent) for r in {**desired, **retained}}
        removed = []
        for relative in sorted(receipt['directories'], key=lambda s: len(Path(s).parts), reverse=True):
            path = base / relative
            if len(Path(relative).parts) != 3 or any(n == relative or n.startswith(relative + '/') for n in needed):
                continue
            if path.is_symlink() or not path.is_dir():
                continue
            try:
                path.rmdir()
                removed.append(relative)
            except OSError:
                pass
        if removed or set(receipt['links']) != set(desired) | set(retained):
            receipt['links'] = {**desired, **retained}
            receipt['directories'] = [d for d in receipt['directories'] if d not in removed]
            temporary = base / (RECEIPT + '.' + uuid.uuid4().hex)
            with temporary.open('x') as stream:
                json.dump(receipt, stream, indent=2)
                stream.write('\n')
            temporary.replace(base / RECEIPT)
        # Still under the base lock, so a racing uninstall cannot be undone by this entry.
        record(add=[str(base)])
    for short, name in sorted(names.items()):
        if name != short:
            print(f'{short}: that name is already taken here, so it installs as {name}.')
    print(f'Installed {len(desired)} links for {", ".join(runtimes)} in {base}')
    print('Open a new session. Claude Code: /opascope | Codex: $opascope')
    print('Try: define done for sorting a folder of notes without losing any.')
    print(f'Uninstall: python3 "{ROOT / "install.py"}" uninstall --base "{base}" --yes')


def uninstall(base):
    base = base.resolve(strict=True)
    with checkout_locked(), locked(base):
        receipt = read_receipt(base)
        if not receipt:
            record(remove=[str(base)])
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
        record(remove=[str(base)])
    print('Removed installed links, receipt and empty directories created by the installer.')
    print('Source checkout and task artifacts retained.')
    if preserved:
        print('Preserved user changes or nonempty directories:\n' + '\n'.join(sorted(set(preserved))))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install', 'uninstall', 'status', 'forget'], nargs='?', default='install')
    parser.add_argument('--runtime', choices=['claude', 'codex', 'both'])
    parser.add_argument('--base', type=Path, help='Home directory for global install, or project directory for local install')
    parser.add_argument('--yes', action='store_true', help='Use explicit flags or defaults without prompts')
    args = parser.parse_args(argv)
    base = args.base
    runtime = args.runtime
    if args.action == 'forget':
        if base is None:
            raise ValueError('Name the place to forget with --base.')
        change_index(remove=[str(base.expanduser().resolve())])
        print(f'Forgot {base}. Nothing there was changed.')
        return 0
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
    if args.action == 'status' and base is None:
        found, stale = installed_bases()
        report = {'installed': bool(found),
                  'installs': [{'base': b, 'runtimes': r.get('runtimes', [])} for b, r in found]}
        if stale:
            report['stale'] = stale
        try:
            read_index()
        except ValueError as exc:
            report['problem'] = f'{exc}. Only your home and current folder were checked.'
        if not found:
            report['note'] = 'Not installed anywhere from this checkout.'
        print(json.dumps(report, indent=2))
        return 0
    base = (base or Path.home()).expanduser()
    if args.action == 'install':
        install(base, ['claude', 'codex'] if (runtime or 'both') == 'both' else [runtime])
    elif args.action == 'uninstall':
        uninstall(base)
    else:
        print(json.dumps(read_receipt(base) or {'installed': False, 'base': str(base.resolve())}, indent=2))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)
