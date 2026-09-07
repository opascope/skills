"""Each skill states its promise and the checks that would catch it breaking it.

A skill is instructions, so the only honest proof is running it and reading what
comes back. These contracts turn that reading into pass or fail. Every contract
carries at least one adversarial check: the fixture plants something the skill is
supposed to refuse, and the check fails if the skill did it anyway. Adding a
skill means adding its promise.json beside it, never editing a test."""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
SURFACES = ('artifact', 'output', 'project')
CHECKS = ('present', 'absent', 'at_least', 'absent_path')


def contracts():
    found = {}
    for path in sorted((ROOT / 'skills').glob('*/promise.json')):
        found[path.parent.name] = json.loads(path.read_text())
    return found


def wellformed(name, contract):
    """Structural problems in the contract itself, before anything is run."""
    problems = []
    for field in ('promise', 'fixture', 'assertions'):
        if not contract.get(field):
            problems.append(f'{name}: missing "{field}"')
    promise = contract.get('promise', '')
    if promise and (len(promise.split()) > 25 or '\n' in promise):
        problems.append(f'{name}: the promise must be one short sentence')
    if not contract.get('fixture', {}).get('request'):
        problems.append(f'{name}: fixture needs a request to send')
    assertions = contract.get('assertions', [])
    seen = set()
    for assertion in assertions:
        aid = assertion.get('id', '')
        if not aid or aid in seen:
            problems.append(f'{name}: assertion ids must be present and unique')
        seen.add(aid)
        if not assertion.get('why'):
            problems.append(f'{name}/{aid}: say why this check exists')
        if assertion.get('on') not in SURFACES:
            problems.append(f'{name}/{aid}: "on" must be one of {", ".join(SURFACES)}')
        used = [c for c in CHECKS if c in assertion]
        if len(used) != 1:
            problems.append(f'{name}/{aid}: use exactly one of {", ".join(CHECKS)}')
        if 'at_least' in assertion and 'per' not in assertion:
            problems.append(f'{name}/{aid}: "at_least" needs "per"')
        for field in ('present', 'absent', 'at_least', 'per'):
            if field in assertion:
                try:
                    re.compile(assertion[field])
                except re.error as exc:
                    problems.append(f'{name}/{aid}: bad pattern in "{field}": {exc}')
    if not any(a.get('adversarial') for a in assertions):
        problems.append(f'{name}: needs at least one adversarial check, one that '
                        f'fails when the skill does something it promised not to')
    return problems


def count(pattern, text):
    return len(re.findall(pattern, text, re.I | re.M))


def blocks(per, text):
    """One chunk per `per` match, so a check can be made to hold for each.

    Counting matches across the whole document let a plan put three checks on one
    step and none on the next two and still pass, because the totals balanced.
    Anything before the first match is preamble and belongs to no step.
    """
    starts = [m.start() for m in re.finditer(per, text, re.I | re.M)]
    return [text[start:end] for start, end in zip(starts, starts[1:] + [len(text)])]


def evaluate(contract, artifact='', output='', project=None):
    """Run one contract's checks against what a real run produced."""
    results = []
    surfaces = {'artifact': artifact, 'output': output}
    for assertion in contract.get('assertions', []):
        surface = assertion.get('on')
        if 'absent_path' in assertion:
            target = Path(project or '.') / assertion['absent_path']
            passed, detail = not target.exists(), f'{assertion["absent_path"]} exists'
        elif 'present' in assertion:
            hits = count(assertion['present'], surfaces.get(surface, ''))
            passed = hits >= assertion.get('min', 1)
            detail = f'found {hits}, needed {assertion.get("min", 1)}'
        elif 'absent' in assertion:
            hits = count(assertion['absent'], surfaces.get(surface, ''))
            passed, detail = hits == 0, f'found {hits}, needed 0'
        else:
            chunks = blocks(assertion['per'], surfaces.get(surface, ''))
            missing = [n for n, chunk in enumerate(chunks, 1)
                       if not count(assertion['at_least'], chunk)]
            # No blocks at all is a failure, not a pass. A plan written without the
            # headings these checks divide on used to satisfy every one of them.
            passed = bool(chunks) and not missing
            detail = (f'nothing matched {assertion["per"]}' if not chunks
                      else 'step(s) ' + ', '.join(map(str, missing)) +
                           f' have no {assertion["at_least"]}')
        results.append({'id': assertion.get('id'), 'passed': passed,
                        'adversarial': bool(assertion.get('adversarial')),
                        'why': assertion.get('why'), 'detail': '' if passed else detail})
    return results


def read_artifacts(project):
    """Every text file a run left in the working directory, as one blob."""
    work = Path(project) / '.opascope-work'
    text = []
    for path in sorted(work.rglob('*')):
        if path.is_file() and path.suffix in ('.md', '.json', '.txt'):
            try:
                text.append(path.read_text())
            except (OSError, UnicodeError):
                continue
    return '\n'.join(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['check', 'list'])
    args = parser.parse_args()
    found = contracts()
    if args.command == 'list':
        for name, contract in found.items():
            adversarial = sum(1 for a in contract['assertions'] if a.get('adversarial'))
            print(f'{name}\n  promise: {contract["promise"]}\n  '
                  f'{len(contract["assertions"])} checks, {adversarial} adversarial')
        return 0
    installed = {p.name for p in (ROOT / 'skills').iterdir() if (p / 'SKILL.md').is_file()}
    problems = [f'{name}: installed with no promise.json' for name in sorted(installed - set(found))]
    for name, contract in found.items():
        problems += wellformed(name, contract)
    for problem in problems:
        print('FAIL  ' + problem)
    print(f'{len(found)} contract(s), {len(problems)} problem(s)')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
