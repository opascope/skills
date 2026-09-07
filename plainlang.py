"""Gate the words a reader sees. Skill instructions may use trade terms; names,
promises and the README may not. Runs offline over the checkout, reads only."""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent

# Words that only mean something once you already work this way. Each entry is
# paired with the plain wording that replaces it, so a failure teaches the fix
# instead of just refusing the word.
INSIDER = {
    'artifact': 'file, or name the file',
    'artifacts': 'files, or name the files',
    'agentic': 'run by an agent',
    'atomic': 'small enough to finish in one step',
    'bounded': 'with a limit you set',
    'canonical': 'the one true copy',
    'checkpoint': 'saved progress',
    'cold': 'from nothing, with no memory of the last session',
    'compaction': 'the point where a long chat is summarized',
    'context window': 'how much a session can hold at once',
    'deterministic': 'gives the same answer every time',
    'drift': 'work wandering off the request',
    'durable': 'saved to a file',
    'falsifiable': 'you can check it as true or false',
    'fast-forward': 'moves forward to a released version',
    'harness': 'the program running the agent',
    'idempotent': 'safe to run twice',
    'namespaced': 'named with a shared prefix',
    'preamble': 'the opening instructions',
    'provenance': 'where it came from',
    'quota': 'your usage allowance',
    'receipt': 'the record of what was installed',
    'spec': 'the written description of the work',
    'token': 'a unit of model usage',
    'tokens': 'units of model usage',
}
# Saying how many skills there are dates the sentence the day one is added.
FIXED_COUNT = re.compile(
    r'\b(one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+'
    r'(work-process\s+|process\s+)?skills\b', re.I)
# Names that must not appear anywhere in the package. Reading how another
# project was put together is not a reason to advertise it in our own docs.
NO_MENTION = ('examplepkg', 'exampleuser')
GRADE_CEILING = 8.0
SENTENCE_CEILING = 17.0
NAME_TOKENS = (1, 3)


def skills():
    return sorted(p for p in (ROOT / 'skills').iterdir() if (p / 'SKILL.md').is_file())


def description(skill):
    try:
        header = (skill / 'SKILL.md').read_text().split('---', 2)[1]
    except (OSError, IndexError, UnicodeError):
        return ''
    match = re.search(r'^description:\s*(.+)$', header, re.M)
    return match.group(1).strip() if match else ''


def prose(text):
    """Drop the parts a reader does not read as sentences."""
    text = re.sub(r'```.*?```', ' ', text, flags=re.S)
    text = re.sub(r'`[^`]*`', ' ', text)
    text = '\n'.join(line for line in text.splitlines()
                     if not line.lstrip().startswith(('|', '#', '>')))
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    return re.sub(r'https?://\S+', ' ', text)


def syllables(word):
    word = re.sub(r'[^a-z]', '', word.lower())
    if not word:
        return 0
    count = len(re.findall(r'[aeiouy]+', word))
    if word.endswith('e') and not word.endswith(('le', 'ee')) and count > 1:
        count -= 1
    return max(count, 1)


def readability(text):
    sentences = [s for s in re.split(r'[.!?]+(?:\s|$)', text) if s.strip()]
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", text)
    if not sentences or not words:
        return None
    per_sentence = len(words) / len(sentences)
    per_word = sum(syllables(w) for w in words) / len(words)
    return {'grade': round(0.39 * per_sentence + 11.8 * per_word - 15.59, 1),
            'words_per_sentence': round(per_sentence, 1),
            'words': len(words), 'sentences': len(sentences)}


def insider_hits(text):
    found = {}
    for word in INSIDER:
        pattern = re.escape(word).replace(r'\ ', r'\s+')
        hits = len(re.findall(r'(?<![\w-])' + pattern + r'(?![\w-])', text, re.I))
        if hits:
            found[word] = hits
    return found


def parallel_runs(text):
    """Three sentences opening the same way is a rhythm a person does not write.

    This is the tell that reads as machine-written even when every word is plain:
    "It does not wake. It does not retry. It does not handle." The writing-guidance
    detectors miss it, because each sentence on its own is unremarkable.
    """
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
    opener = lambda s, n: ' '.join(re.findall(r"[A-Za-z][A-Za-z'-]*", s)[:n]).lower()
    found, run = [], []
    for sentence in sentences + ['']:
        two = opener(sentence, 2)
        if run and two and two == opener(run[-1], 2):
            run.append(sentence)
            continue
        if len(run) >= 3:
            found.append((opener(run[0], 2), len(run)))
        run = [sentence] if sentence else []
    return found


def test_count():
    """How many tests actually exist, so the README cannot claim a stale number."""
    return sum(f.read_text().count('def test_') for f in (ROOT / 'tests').glob('test_*.py'))


def claimed_test_counts(readme):
    return {int(n) for n in re.findall(r'tests-(\d+)%20passing', readme)} | \
           {int(n) for n in re.findall(r'(\d+)\s+standard-library\s+tests', readme)}


def outside_mentions(text):
    return [name for name in NO_MENTION
            if re.search(r'(?<![\w-])' + re.escape(name) + r'(?![\w-])', text, re.I)]


def fail(findings, surface, message, fix=''):
    findings.append({'severity': 'fail', 'surface': surface, 'message': message, 'fix': fix})


def warn(findings, surface, message, fix=''):
    findings.append({'severity': 'warn', 'surface': surface, 'message': message, 'fix': fix})


def check():
    findings = []
    readme = (ROOT / 'README.md').read_text()
    readme_prose = prose(readme)

    for word, hits in insider_hits(readme_prose).items():
        fail(findings, 'README.md', f'trade term "{word}" appears {hits} time(s)',
             f'say "{INSIDER[word]}"')

    for opener, count in parallel_runs(readme_prose):
        fail(findings, 'README.md',
             f'{count} sentences in a row open with "{opener}"',
             'vary the opening, or join them into one sentence')

    actual = test_count()
    for claimed in sorted(claimed_test_counts(readme)):
        if claimed != actual:
            fail(findings, 'README.md',
                 f'claims {claimed} tests, but {actual} exist',
                 f'say {actual}, or add the missing tests')

    score = readability(readme_prose)
    if score and score['grade'] > GRADE_CEILING:
        fail(findings, 'README.md',
             f'reading grade {score["grade"]} is above {GRADE_CEILING}',
             'shorter sentences and shorter words')
    if score and score['words_per_sentence'] > SENTENCE_CEILING:
        fail(findings, 'README.md',
             f'{score["words_per_sentence"]} words per sentence is above {SENTENCE_CEILING}',
             'split the long sentences')

    for path in [ROOT / 'README.md', ROOT / 'shared.md', ROOT / 'ARCHITECTURE.md']:
        if not path.is_file():
            continue
        for match in FIXED_COUNT.finditer(path.read_text()):
            fail(findings, path.name, f'states a fixed number of skills: "{match.group(0)}"',
                 'write it so adding a skill does not date the sentence')

    for path in sorted(ROOT.glob('*.md')) + sorted(ROOT.glob('docs/*.md')) + \
            sorted(ROOT.glob('skills/*/**/*.md')):
        for name in outside_mentions(path.read_text()):
            fail(findings, str(path.relative_to(ROOT)),
                 f'mentions "{name}"', 'this package does not reference other packages')

    table = {name: sentence.strip() for name, sentence in re.findall(
        r'^\|\s*`?([a-z][a-z0-9-]*)`?\s*\|\s*(.+?)\s*\|\s*$', readme, re.M)}
    promised = {}
    for path in (ROOT / 'skills').glob('*/promise.json'):
        try:
            promised[path.parent.name] = json.loads(path.read_text()).get('promise', '')
        except (OSError, ValueError):
            continue
    for skill in skills():
        name = skill.name
        if name == 'opascope':
            continue
        if not name.startswith('opascope-'):
            fail(findings, name, 'skill directory is missing the opascope- prefix')
            continue
        bare = name[len('opascope-'):]
        parts = bare.split('-')
        if not NAME_TOKENS[0] <= len(parts) <= NAME_TOKENS[1]:
            fail(findings, name, f'name has {len(parts)} words, keep it to '
                                 f'{NAME_TOKENS[0]} to {NAME_TOKENS[1]}')
        for part in parts:
            if part in INSIDER:
                fail(findings, name, f'name uses the trade term "{part}"',
                     f'"{part}" means {INSIDER[part]}; name it that way')
            if len(part) < 3:
                fail(findings, name, f'"{part}" is too short to read as a word')
        if bare not in table:
            fail(findings, name, 'name is missing from the README table',
                 'every installed skill is listed with one plain sentence')
        elif promised.get(name) and table[bare] != promised[name]:
            fail(findings, name, 'the README sentence is not the promise it is tested against',
                 f'use: {promised[name]}')
        for word, hits in insider_hits(description(skill)).items():
            warn(findings, name + ' description',
                 f'trade term "{word}" appears {hits} time(s)', f'say "{INSIDER[word]}"')

    return findings, score


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--strict', action='store_true', help='treat warnings as failures')
    args = parser.parse_args()
    findings, score = check()
    failures = [f for f in findings if f['severity'] == 'fail']
    warnings = [f for f in findings if f['severity'] == 'warn']
    if args.json:
        print(json.dumps({'readability': score, 'findings': findings}, indent=2))
    else:
        for finding in findings:
            line = f'{finding["severity"].upper()}  {finding["surface"]}: {finding["message"]}'
            print(line + (f' -> {finding["fix"]}' if finding['fix'] else ''))
        if score:
            print(f'README reading grade {score["grade"]}, '
                  f'{score["words_per_sentence"]} words per sentence')
        print(f'{len(failures)} failure(s), {len(warnings)} warning(s)')
    return 1 if failures or (args.strict and warnings) else 0


if __name__ == '__main__':
    sys.exit(main())
