#!/usr/bin/env python3
"""One small OpenRouter client: chat, research, pick, decide, embed, models and key.

When no model is named, Jev picks one per request. Every call reports the model
that answered and what it cost. Standard library only.
"""
import argparse
import base64
import json
import mimetypes
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

API = 'https://openrouter.ai/api/v1'
DECISIONS = 'https://openrouter.ai/api/alpha/decisions'
ROLES_FILE = Path(__file__).resolve().parent / 'roles.json'
TIMEOUT = 180
urlopen = urllib.request.urlopen  # Replaced in tests; nothing here needs the network otherwise.


class Failure(Exception):
    """A failure the user can act on. The message is printed as is."""


def load_roles():
    data = json.loads(ROLES_FILE.read_text())
    return data['roles'], data.get('router', {})


def role_model(roles, role):
    if role not in roles:
        raise Failure(f'Unknown role "{role}". Roles: {", ".join(sorted(roles))}.')
    override = os.environ.get('OPENROUTER_MODEL_' + role.upper().replace('-', '_'))
    return override or roles[role]['model']


def choose(roles, router, verb, model=None, role=None, json_mode=False, images=False):
    """The model for this call and who chose it. First match wins."""
    if model:
        return model, '--model', None
    if role:
        return role_model(roles, role), f'role {role}', roles.get(role, {}).get('effort')
    implied = {'research': 'sonar', 'research-deep': 'sonar-pro', 'embed': 'embed', 'decide': 'decision'}
    if verb in implied:
        name = implied[verb]
        return role_model(roles, name), f'role {name}', roles[name].get('effort')
    for flag, used in (('json', json_mode), ('image', images)):
        if used and not router.get(flag):
            return (role_model(roles, 'fast'), f'role fast (router does not support --{flag})',
                    roles['fast'].get('effort'))
    return role_model(roles, 'auto'), 'jev-router', None


def api_key():
    key = os.environ.get('OPENROUTER_API_KEY', '').strip()
    if not key:
        raise Failure('OPENROUTER_API_KEY is not set. Create a key at https://openrouter.ai/keys '
                      'and export it. The models verb works without one.')
    return key


def request(url, body=None, key=None):
    headers = {'Content-Type': 'application/json', 'X-Title': 'opascope openrouter skill'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method='GET' if body is None else 'POST')
    try:
        with urlopen(req, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode(errors='replace')
        except Exception:
            detail = ''
        raise Failure(explain(exc.code, detail, (body or {}).get('model')))
    except urllib.error.URLError as exc:
        raise Failure(f'Could not reach OpenRouter: {exc.reason}')


def explain(code, detail, model):
    message = ''
    try:
        message = json.loads(detail).get('error', {}).get('message', '')
    except (ValueError, AttributeError):
        pass
    if key_in(message):
        message = ''
    if code == 401:
        return 'OpenRouter refused the key (401). Check OPENROUTER_API_KEY.'
    if code == 402:
        return 'The key has no credit left (402). Add credit at https://openrouter.ai/settings/credits.'
    if code == 429:
        return 'OpenRouter is rate limiting this key (429). Wait and try again.'
    if code == 404 and any(w in (message + detail).lower() for w in ('data policy', 'zdr', 'retention')):
        return (f'Your account privacy setting blocks {model} (404). It is in the catalog; '
                'change the data policy at https://openrouter.ai/settings/privacy or pick another model.')
    return f'OpenRouter returned HTTP {code}' + (f': {message}' if message else '.')


def key_in(text):
    key = os.environ.get('OPENROUTER_API_KEY', '').strip()
    return bool(key) and key in text


def cost_of(usage):
    """The real cost the response states, or None. Never an estimate."""
    usage = usage or {}
    if usage.get('is_byok'):
        value = (usage.get('cost_details') or {}).get('upstream_inference_cost')
    else:
        value = usage.get('cost')
    return value if isinstance(value, (int, float)) else None


def money(value):
    if value is None:
        return 'unknown'
    return '$' + (('%.10f' % value).rstrip('0').rstrip('.') or '0')


def trailer(model, chosen_by, usage):
    usage = usage or {}
    tokens = f'{usage.get("prompt_tokens", usage.get("input_tokens", "?"))}/' \
             f'{usage.get("completion_tokens", usage.get("output_tokens", "?"))}'
    return f'model: {model} | chosen by: {chosen_by} | cost: {money(cost_of(usage))} | tokens: {tokens}'


def image_part(path):
    kind = mimetypes.guess_type(str(path))[0] or 'image/png'
    data = base64.b64encode(Path(path).read_bytes()).decode()
    return {'type': 'image_url', 'image_url': {'url': f'data:{kind};base64,{data}'}}


def chat_call(model, prompt, key, system=None, json_mode=False, max_tokens=None, effort=None, images=()):
    content = prompt if not images else [{'type': 'text', 'text': prompt}] + [image_part(p) for p in images]
    messages = ([{'role': 'system', 'content': system}] if system else []) + [{'role': 'user', 'content': content}]
    body = {'model': model, 'messages': messages, 'usage': {'include': True}}
    if json_mode:
        body['response_format'] = {'type': 'json_object'}
    if max_tokens:
        body['max_tokens'] = max_tokens
    if effort == 'none':
        body['reasoning'] = {'enabled': False}
    elif effort:
        body['reasoning'] = {'effort': effort}
    data = request(API + '/chat/completions', body, key)
    choice = (data.get('choices') or [{}])[0]
    text = (choice.get('message') or {}).get('content') or ''
    if not text.strip() and choice.get('finish_reason') == 'length':
        # A reasoning model can spend a small budget thinking and say nothing.
        budget = (max_tokens or 1024) * 2
        body['reasoning'] = {'enabled': False}
        body['max_tokens'] = budget
        data = request(API + '/chat/completions', body, key)
        choice = (data.get('choices') or [{}])[0]
        text = (choice.get('message') or {}).get('content') or ''
        if not text.strip():
            raise Failure(f'{data.get("model") or model} returned an empty reply twice, the second time with '
                          f'reasoning off and {budget} tokens. Raise --max-tokens or pick another model.')
    return text, data


def citations(data, text):
    urls = list(data.get('citations') or [])
    for choice in data.get('choices') or []:
        for note in (choice.get('message') or {}).get('annotations') or []:
            url = (note.get('url_citation') or {}).get('url')
            if url and url not in urls:
                urls.append(url)
    return urls


def emit(args, answer, model, chosen_by, usage, generation=None, extra=None):
    line = trailer(model, chosen_by, usage)
    if getattr(args, 'receipt', None):
        Path(args.receipt).write_text(line + (f' | id: {generation}' if generation else '') + '\n')
    if getattr(args, 'json_out', False):
        record = {'answer': answer, 'model': model, 'chosen_by': chosen_by,
                  'cost': cost_of(usage), 'usage': usage or {}, 'id': generation}
        record.update(extra or {})
        print(json.dumps(record, indent=2))
    else:
        if answer is not None:
            print(answer)
        print(line, file=sys.stderr)


def cmd_chat(args, roles, router):
    model, chosen_by, effort = choose(roles, router, 'chat', args.model, args.role, args.json, bool(args.image))
    text, data = chat_call(model, args.prompt, api_key(), args.system, args.json, args.max_tokens,
                           args.effort or effort, args.image or ())
    emit(args, text, data.get('model') or model, chosen_by, data.get('usage'), data.get('id'))


def cmd_research(args, roles, router):
    model, chosen_by, effort = choose(roles, router, 'research-deep' if args.deep else 'research',
                                      args.model, args.role)
    text, data = chat_call(model, args.question, api_key(), max_tokens=args.max_tokens, effort=effort)
    urls = citations(data, text)
    shown = text + ('\n\nSources:\n' + '\n'.join(f'[{i}] {u}' for i, u in enumerate(urls, 1)) if urls else '')
    emit(args, shown, data.get('model') or model, chosen_by, data.get('usage'), data.get('id'), {'citations': urls})


def cmd_pick(args, roles, router):
    model, chosen_by, _ = choose(roles, router, 'chat')
    _, data = chat_call(model, args.prompt, api_key(), max_tokens=args.max_tokens)
    picked = data.get('model') or model
    emit(args, picked, picked, chosen_by, data.get('usage'), data.get('id'))


def cmd_decide(args, roles, router):
    model, chosen_by, _ = choose(roles, router, 'decide', args.model)
    state = sys.stdin.read() if args.state == '-' else Path(args.state).read_text()
    questions = json.loads(Path(args.questions).read_text())
    data = request(DECISIONS, {'model': model, 'state': state, 'questions': questions}, api_key())
    emit(args, json.dumps(data.get('answers') or {}, indent=2), data.get('model') or model, chosen_by,
         data.get('usage'), data.get('id'))


def cmd_embed(args, roles, router):
    model, chosen_by, _ = choose(roles, router, 'embed', args.model)
    data = request(API + '/embeddings', {'model': model, 'input': args.text}, api_key())
    vector = ((data.get('data') or [{}])[0]).get('embedding') or []
    answer = json.dumps(vector) if args.json else f'{len(vector)} dimensions'
    emit(args, answer, data.get('model') or model, chosen_by, data.get('usage'), data.get('id'))


def catalog():
    ids = {}
    for path in ('/models', '/embeddings/models'):
        for row in request(API + path).get('data') or []:
            ids[row['id']] = row
    return ids


def cmd_models(args, roles, router):
    action = args.action or 'roles'
    if action == 'roles':
        for name, role in roles.items():
            print(f'{name:10} {role_model(roles, name):36} {role.get("description", "")}')
        return 0
    rows = catalog()
    if action == 'check':
        import time
        problems = 0
        for name, role in roles.items():
            if role.get('checked_by') == 'decisions-endpoint':
                print(f'{name}: skipped (decisions endpoint)')
                continue
            model = role_model(roles, name)
            row = rows.get(model)
            expires = (row or {}).get('expiration_date')
            if not row:
                print(f'{name}: missing ({model})')
                problems += 1
            elif expires and str(expires)[:10] < time.strftime('%Y-%m-%d'):
                print(f'{name}: expired on {expires} ({model})')
                problems += 1
            else:
                print(f'{name}: ok ({model})')
        return 1 if problems else 0
    found = list(rows.values())
    if args.search:
        q = args.search.lower()
        found = [r for r in found if q in r['id'].lower() or q in (r.get('name') or '').lower()]
    found.sort(key=lambda r: (-(r.get('created') or 0)) if args.newest else r['id'])
    for row in found[:args.limit]:
        price = row.get('pricing') or {}
        print(f'{row["id"]:50} context {row.get("context_length", "?"):>8}  '
              f'in {price.get("prompt", "?")}  out {price.get("completion", "?")} per token')
    return 0


def cmd_key(args, roles, router):
    data = request(API + '/key', key=api_key()).get('data') or {}
    shown = {k: data.get(k) for k in ('label', 'limit', 'limit_remaining', 'usage', 'is_free_tier') if k in data}
    print(json.dumps(shown, indent=2))
    return 0


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='verb', required=True, metavar='{chat,research,pick,decide,embed,models,key}')

    def common(q):
        q.add_argument('--json-out', action='store_true', help='Print one JSON object instead of text')
        q.add_argument('--receipt', help='Also write the trailer line and generation id to this file')

    q = sub.add_parser('chat', help='Ask a model. Jev picks one when you name none')
    q.add_argument('prompt')
    q.add_argument('--role')
    q.add_argument('--model')
    q.add_argument('--system')
    q.add_argument('--json', action='store_true', help='Ask for a JSON object')
    q.add_argument('--max-tokens', type=int)
    q.add_argument('--effort', choices=['none', 'low', 'medium', 'high'])
    q.add_argument('--image', action='append', help='Attach an image file; repeat as needed')
    common(q)
    q = sub.add_parser('research', help='Web research with numbered citations')
    q.add_argument('question')
    q.add_argument('--deep', action='store_true')
    q.add_argument('--role')
    q.add_argument('--model')
    q.add_argument('--max-tokens', type=int)
    common(q)
    q = sub.add_parser('pick', help='Route one sample and print the model Jev chose')
    q.add_argument('prompt')
    q.add_argument('--max-tokens', type=int)
    common(q)
    q = sub.add_parser('decide', help='Closed questions answered by Jev, with confidence')
    q.add_argument('--state', required=True, help='File with the context, or - for stdin')
    q.add_argument('--questions', required=True, help='JSON file of closed questions')
    q.add_argument('--model')
    common(q)
    q = sub.add_parser('embed', help='Embed text')
    q.add_argument('text')
    q.add_argument('--model')
    q.add_argument('--json', action='store_true', help='Print the vector')
    common(q)
    q = sub.add_parser('models', help='Roles, the catalog, or a check of every role. No key needed')
    q.add_argument('action', nargs='?', choices=['list', 'check'])
    q.add_argument('--search')
    q.add_argument('--newest', action='store_true')
    q.add_argument('--limit', type=int, default=40)
    sub.add_parser('key', help='Credit left on the key')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    handlers = {'chat': cmd_chat, 'research': cmd_research, 'pick': cmd_pick, 'decide': cmd_decide,
                'embed': cmd_embed, 'models': cmd_models, 'key': cmd_key}
    try:
        roles, router = load_roles()
        return handlers[args.verb](args, roles, router) or 0
    except Failure as exc:
        text = str(exc)
        print('Error: ' + ('(message hidden: it contained the key)' if key_in(text) else text), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
