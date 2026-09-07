"""Read transcript records, never execute their contents or transmit them."""
from collections import defaultdict
import json
from pathlib import Path
import re

LIMITATION = ('Counts only explicit by-name invocations, once per skill per session. '
              'Undercounts skills that self-select from their description. '
              'Read-only and local-only: no transcript content is transmitted.')
TOKEN = re.compile(r'(?<![\w:/])[$/]([a-z][a-z0-9-]*)(?![\w/.-])')
BUILTINS = {'help', 'exit', 'quit', 'clear', 'compact', 'model', 'effort',
            'resume', 'status', 'permissions', 'config', 'init', 'login', 'logout',
            'copy', 'goal', 'commands', 'usage', 'usage-credits'}


def catalog():
    """Read only entrypoint metadata; never traverse entire linked repositories."""
    roots = [Path(__file__).resolve().parent / 'skills']
    for base in (Path.home(), Path.cwd()):
        roots += [base / '.claude/skills', base / '.agents/skills', base / '.codex/skills']
    names = set()
    for root in roots:
        for skill in root.glob('*/SKILL.md'):
            try:
                header = skill.read_text().split('---', 2)[1]
                match = re.search(r'^name:\s*[\"\']?([a-z][a-z0-9-]*)[\"\']?\s*$', header, re.M)
                if match:
                    names.add(match.group(1))
            except (OSError, IndexError, UnicodeError):
                continue
    return names


def user_text(record):
    if record.get('type') == 'user':
        content = record.get('message', {}).get('content', '')
    elif record.get('type') == 'response_item' and record.get('payload', {}).get('role') == 'user':
        content = record['payload'].get('content', [])
    elif record.get('type') == 'event_msg' and record.get('payload', {}).get('type') == 'user_message':
        content = record['payload'].get('message', '')
    else:
        return ''
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(c.get('text', '') for c in content if isinstance(c, dict) and c.get('type') in ('text', 'input_text'))
    return ''


def invocations(record, known=None):
    found = set()
    text = user_text(record)
    # Injected instruction catalogues and quoted code are not user invocations.
    if text and not any(marker in text for marker in ('<environment_context>', '<INSTRUCTIONS>', '<skills_instructions>', '<available_skills>')):
        found.update(name for name in re.findall(r'<command-name>/?([a-z][a-z0-9-]*)</command-name>', text) if name not in BUILTINS or (known and name in known))
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'```.*?```', '', text, flags=re.S)
        text = re.sub(r'`[^`]*`', '', text)
        found.update(name for name in TOKEN.findall(text) if name not in BUILTINS or (known and name in known))
        if known is not None:
            found.intersection_update(known)
    if record.get('type') == 'assistant':
        content = record.get('message', {}).get('content', [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get('type') == 'tool_use' and block.get('name') == 'Skill':
                    name = block.get('input', {}).get('skill', '')
                    if re.fullmatch(r'[a-z][a-z0-9:-]*', name) and (name not in BUILTINS or (known and name in known)):
                        found.add(name)
    return found


def measure(paths, names=None):
    known = catalog() | set(names or [])
    files = set()
    missing = 0
    for path in paths:
        path = path.expanduser()
        if path.is_file():
            files.add(path.resolve())
        elif path.is_dir():
            files.update(p.resolve() for p in path.rglob('*.jsonl') if p.is_file())
        else:
            missing += 1
    counts = defaultdict(set)
    sessions = set()
    invalid = unreadable = recognized = 0
    for path in sorted(files):
        identity = str(path)
        names = set()
        try:
            with path.open(errors='replace') as stream:
                for line in stream:
                    try:
                        record = json.loads(line)
                        if not isinstance(record, dict):
                            raise ValueError()
                    except (ValueError, TypeError):
                        invalid += 1
                        continue
                    if isinstance(record.get('sessionId'), str):
                        identity = 'claude:' + record['sessionId']
                    if record.get('type') == 'session_meta' and isinstance(record.get('payload', {}).get('id'), str):
                        identity = 'codex:' + record['payload']['id']
                    hits = invocations(record, known)
                    recognized += bool(hits)
                    names.update(hits)
        except OSError:
            unreadable += 1
            continue
        sessions.add(identity)
        for name in names:
            counts[name].add(identity)
    return {
        'limitation': LIMITATION,
        'catalog_names': len(known),
        'files': len(files), 'sessions': len(sessions), 'missing_paths': missing,
        'malformed_lines': invalid, 'unreadable_files': unreadable,
        'records_with_invocations': recognized,
        'ranking': [{'skill': name, 'sessions': len(ids)} for name, ids in sorted(counts.items(), key=lambda x: (-len(x[1]), x[0]))],
    }


def report(paths, as_json=False, names=None):
    if not paths:
        paths = [Path.home() / '.claude/projects', Path.home() / '.codex/sessions']
    result = measure(paths, names)
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        print(LIMITATION)
        print(f'Scanned {result["files"]} files, {result["sessions"]} distinct session identities.')
        print(f'Skipped: {result["missing_paths"]} missing paths, {result["malformed_lines"]} malformed lines, {result["unreadable_files"]} unreadable files.')
        for row in result['ranking']:
            print(f'{row["sessions"]:6}  {row["skill"]}')
        if not result['ranking']:
            print('No recognized invocations. Supply transcript files or folders explicitly if stored elsewhere.')
