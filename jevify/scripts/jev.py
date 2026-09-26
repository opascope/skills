#!/usr/bin/env python3
"""Run a Jev job over a CSV or JSONL file. Standard library only.

A job file (JSON) says what to ask about each row:

    {
      "name": "keyword-intent",
      "fields": ["keyword"],                 # row columns sent as state (default: all)
      "shared_state": {"brand": "..."},      # constant context added to every row
      "questions": { "<id>": {type, instructions, criteria}, ... },
      "cutoffs": {"<id>": 0.9}               # confidence needed to act without review
    }

Commands:
    validate JOB [--items FILE]        check the job before spending anything
    run JOB ITEMS [--limit N] [--out F] run it (use --limit 20 for the sample run)
    review RESULTS --question ID       build the 20-row human check sheet
    calibrate SHEET RESULTS --question ID  read the labeled sheet, report accuracy by confidence
                                       (label a choice with the option name, a score with the
                                       level number from 0, a noul with yes or no)

A JOB may be a path, or the name of a ready job in this skill's assets/jobs/ folder.

Keys: OPENROUTER_API_KEY (OpenRouter, the default route) or TYPESAFE_API_KEY (the direct
TypeSafe API, used when only that key is set).
"""
import argparse
import concurrent.futures as cf
import csv
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINTS = {
    # provider: (url, key env var, default model). Order is the default precedence:
    # OpenRouter first; TypeSafe direct only when its key is the one that is set.
    "openrouter": ("https://openrouter.ai/api/alpha/decisions", "OPENROUTER_API_KEY", "~typesafe/jev-latest"),
    "typesafe": ("https://api.typesafe.ai/v1/systemone", "TYPESAFE_API_KEY", "jev-latest"),
}
PROVIDER_ORDER = ("openrouter", "typesafe")
JOBS_DIR = Path(__file__).resolve().parent.parent / "assets" / "jobs"
# Vendor limits (https://docs.typesafe.ai/api.md): choice up to 255 options, score 2 to 10 levels.
MAX_CHOICE_OPTIONS = 255
SCORE_LEVELS = (2, 10)
# Vendor list price per input token; output tokens are free (https://docs.typesafe.ai/models.md).
# Used only for the pre-run estimate.
PRICE_PER_INPUT_TOKEN = 0.042 / 1_000_000
CHARS_PER_TOKEN = 4  # rough English average, estimate only
# Billed input can run near twice the character estimate on short rows, likely per-question
# overhead on the vendor side. Pad so --max-cost is conservative.
ESTIMATE_PAD = 2.0
DEFAULT_CUTOFF = 0.9  # conservative start; replace with the result of `calibrate`
ABSTAIN_WORDS = ("none", "unknown", "unclear", "other", "neither", "abstain", "not_enough", "insufficient", "unsure")
MATH_WORDS = re.compile(r"\b(how many|count|percent|percentage|sum of|average|days between|how long ago|older than|newer than)\b", re.I)


def hide_keys(text):
    for _, env, _ in ENDPOINTS.values():
        key = os.environ.get(env, "").strip()
        if key:
            text = text.replace(key, "[key hidden]")
    return text


def fail(msg):
    sys.exit(f"error: {hide_keys(str(msg))}")


def cost_of(usage):
    """The cost the response states. On the account's own provider key OpenRouter reports
    cost 0 and the real cost in cost_details.upstream_inference_cost."""
    usage = usage or {}
    if usage.get("is_byok"):
        value = (usage.get("cost_details") or {}).get("upstream_inference_cost")
    else:
        value = usage.get("cost")
    return value if isinstance(value, (int, float)) else 0


# ---------- job loading and validation ----------

def job_path(path):
    """A path as given, or a ready job's name looked up in assets/jobs/."""
    if os.path.exists(path):
        return path
    for candidate in (JOBS_DIR / path, JOBS_DIR / (path + ".json")):
        if candidate.is_file():
            return str(candidate)
    return path


def load_job(path):
    path = job_path(path)
    try:
        with open(path) as f:
            job = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        fail(f"cannot read job file {path}: {e}")
    if not isinstance(job.get("questions"), dict) or not job["questions"]:
        fail("job needs a non-empty 'questions' object")
    return job


def check_job(job, header=None):
    """Return (errors, warnings). Errors would make the API reject the request."""
    errors, warnings = [], []
    for qid, q in job["questions"].items():
        t, crit = q.get("type"), q.get("criteria")
        where = f"question '{qid}'"
        if t not in ("choice", "score", "noul"):
            errors.append(f"{where}: type must be choice, score or noul (Jev cannot write text)")
            continue
        if not q.get("instructions"):
            errors.append(f"{where}: needs instructions")
        text = json.dumps(q.get("instructions", ""))
        if MATH_WORDS.search(text):
            warnings.append(f"{where}: looks like counting, math or date logic. Jev is weak at these; do them in code")
        if t == "choice":
            if not isinstance(crit, dict) or len(crit) < 2:
                errors.append(f"{where}: choice needs a criteria object with 2+ options")
            elif len(crit) > MAX_CHOICE_OPTIONS:
                errors.append(f"{where}: choice allows at most {MAX_CHOICE_OPTIONS} options")
            elif not any(w in k.lower() for k in crit for w in ABSTAIN_WORDS):
                warnings.append(f"{where}: no 'none / unclear' option. Jev picks a wrong label when nothing fits")
        elif t == "score":
            lo, hi = SCORE_LEVELS
            if not isinstance(crit, list) or not lo <= len(crit) <= hi:
                errors.append(f"{where}: score criteria must be a list of {lo} to {hi} levels, low to high")
            elif any(level in (None, "") for level in crit):
                errors.append(f"{where}: every score level needs a description")
        elif t == "noul":
            if crit is not None and set(crit) != {"true", "false"}:
                errors.append(f"{where}: noul criteria must have both 'true' and 'false', or be left out")
            if qid in job.get("cutoffs", {}):
                warnings.append(f"{where}: noul returns no confidence, so its cutoff is applied to distance from 0.5. "
                                "For a real confidence gate use a choice with yes / no / unclear")
    if "REPLACE" in json.dumps(job):
        errors.append("the job still has a REPLACE placeholder; copy the job and replace every REPLACE (brand, offer, rules, options) first")
    for qid in job.get("cutoffs", {}):
        if qid not in job["questions"]:
            errors.append(f"cutoff for unknown question '{qid}'")
    if header is not None:
        missing = [f for f in job.get("fields", []) if f not in header]
        if missing:
            errors.append(f"fields not in the input file: {missing}. Columns are: {header}")
    return errors, warnings


# ---------- input ----------

def read_items(path):
    if path.endswith(".jsonl"):
        with open(path) as f:
            rows = [json.loads(line) for line in f if line.strip()]
        header = list(rows[0]) if rows else []
    else:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows, header = list(reader), reader.fieldnames or []
    if not rows:
        fail(f"no rows in {path}")
    return rows, header


def state_for(job, row):
    fields = job.get("fields") or list(row)
    state = dict(job.get("shared_state", {}))
    state.update({k: row[k] for k in fields if k in row})
    return state


# ---------- API ----------

def pick_provider(forced):
    if forced:
        url, env, model = ENDPOINTS[forced]
        if not os.environ.get(env):
            fail(f"--provider {forced} needs {env} set")
        return forced
    for name in PROVIDER_ORDER:
        if os.environ.get(ENDPOINTS[name][1]):
            return name
    fail("set OPENROUTER_API_KEY (openrouter.ai/keys), or TYPESAFE_API_KEY (console.typesafe.ai/keys) for the direct route")


def call(provider, model, state, questions, retries=3, timeout=60):
    url, env, _ = ENDPOINTS[provider]
    body = json.dumps({"model": model, "state": state, "questions": questions}).encode()
    headers = {"Authorization": f"Bearer {os.environ[env]}", "Content-Type": "application/json"}
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            try:  # vendors return {"error": {"message": ...}}
                detail = json.loads(detail).get("error", {}).get("message", detail)
            except (json.JSONDecodeError, AttributeError):
                pass
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 ** attempt + random.random())
                continue
            raise RuntimeError(hide_keys(f"HTTP {e.code}: {detail}")) from None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries:
                time.sleep(2 ** attempt + random.random())
                continue
            raise RuntimeError(f"network error: {e}") from None


def check_answers(questions, body):
    """Never trust the shape on the vendor's word: verify every answer."""
    answers = body.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise ValueError("answer keys do not match the questions")
    out = {}
    for qid, q in questions.items():
        a = answers[qid]
        if q["type"] == "noul":
            p = a.get("noul")
            if not isinstance(p, (int, float)) or not 0 <= p <= 1:
                raise ValueError(f"{qid}: bad noul value")
            out[qid] = {"answer": round(p, 4), "confidence": round(abs(p - 0.5) * 2, 4)}
            continue
        probs, conf = a.get("probabilities"), a.get("confidence")
        if not isinstance(probs, dict) or not isinstance(conf, (int, float)):
            raise ValueError(f"{qid}: missing probabilities or confidence")
        if abs(sum(probs.values()) - 1) > 0.02:
            raise ValueError(f"{qid}: probabilities do not sum to 1")
        if q["type"] == "choice":
            if set(probs) != set(q["criteria"]) or a.get("choice") not in q["criteria"]:
                raise ValueError(f"{qid}: answer outside the options")
            out[qid] = {"answer": a["choice"], "confidence": round(conf, 4)}
        else:
            out[qid] = {"answer": round(a.get("score", 0), 3), "confidence": round(conf, 4)}
    return out


# ---------- commands ----------

def cmd_validate(args):
    job = load_job(args.job)
    header = read_items(args.items)[1] if args.items else None
    errors, warnings = check_job(job, header)
    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"ERROR: {e}")
    print("job is valid" if not errors else f"{len(errors)} error(s): fix before running")
    return 1 if errors else 0


def cmd_run(args):
    job = load_job(args.job)
    rows, header = read_items(args.items)
    errors, warnings = check_job(job, header)
    if errors:
        fail("job is invalid, run `validate` first: " + "; ".join(errors))
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    if args.limit:
        rows = random.Random(7).sample(rows, min(args.limit, len(rows))) if args.shuffle else rows[: args.limit]
    provider = pick_provider(args.provider)
    model = args.model or job.get("model") or ENDPOINTS[provider][2]
    questions, cutoffs = job["questions"], job.get("cutoffs", {})
    q_chars = len(json.dumps(questions))
    est = (sum(len(json.dumps(state_for(job, r))) + q_chars for r in rows)
           / CHARS_PER_TOKEN * PRICE_PER_INPUT_TOKEN * ESTIMATE_PAD)
    print(f"{len(rows)} rows, {len(questions)} question(s) per row, via {provider} ({model}). "
          f"Estimated cost ${est:.4f}", file=sys.stderr)
    if est > args.max_cost:
        fail(f"estimate ${est:.2f} is over --max-cost ${args.max_cost:.2f}; raise it to proceed")

    def one(row):
        body = call(provider, model, state_for(job, row), questions)
        return check_answers(questions, body), cost_of(body.get("usage"))

    out_path = args.out or re.sub(r"\.(csv|jsonl)$", "", args.items) + f".{job.get('name', 'jev')}.csv"
    started, spent, failed = time.time(), 0.0, 0
    results = [None] * len(rows)
    with cf.ThreadPoolExecutor(args.concurrency) as pool:
        futures = {pool.submit(one, r): i for i, r in enumerate(rows)}
        for fut in cf.as_completed(futures):
            i = futures[fut]
            try:
                results[i], cost = fut.result()
                spent += cost
            except Exception as e:  # keep going; report the row
                failed += 1
                results[i] = {"_error": hide_keys(str(e))}
    cols = list(header)
    for qid in questions:
        cols += [qid, f"{qid}_confidence", f"{qid}_route"]
    cols.append("jev_error")
    tally = {qid: {} for qid in questions}
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for row, res in zip(rows, results):
            line = dict(row)
            if "_error" in res:
                line["jev_error"] = res["_error"]
            else:
                for qid, a in res.items():
                    cut = cutoffs.get(qid, DEFAULT_CUTOFF)
                    line[qid], line[f"{qid}_confidence"] = a["answer"], a["confidence"]
                    line[f"{qid}_route"] = "auto" if a["confidence"] >= cut else "review"
                    if questions[qid]["type"] == "choice":
                        tally[qid][a["answer"]] = tally[qid].get(a["answer"], 0) + 1
                    else:  # score (0 to levels-1) and noul (0 to 1): report the mean
                        tally[qid].setdefault("_values", []).append(float(a["answer"]))
            w.writerow(line)
    for qid, t in tally.items():
        if "_values" in t:
            vals = t.pop("_values")
            t["mean"] = round(sum(vals) / len(vals), 3)
    secs = time.time() - started
    print(json.dumps({
        "rows": len(rows), "failed": failed, "seconds": round(secs, 1), "cost_usd": round(spent, 6),
        "answers": tally, "out": out_path,
        "auto_share": {q: round(sum(1 for r in results if "_error" not in r
                                    and r[q]["confidence"] >= cutoffs.get(q, DEFAULT_CUTOFF)) / max(1, len(rows) - failed), 3)
                       for q in questions},
    }, indent=2))
    return 1 if failed == len(rows) else 0


def cmd_review(args):
    """Pick rows for a blind human check: mostly low-confidence, some high-confidence.

    Labeling only the uncertain rows misses the case where Jev is confidently wrong,
    so the sheet always includes confident rows too (default 14 low, 6 high)."""
    rows, header = read_items(args.results)
    q = args.question
    if q not in header:
        fail(f"question '{q}' not in {args.results}")
    ok = [r for r in rows if r.get(q) not in (None, "") and not r.get("jev_error")]
    ok.sort(key=lambda r: float(r[f"{q}_confidence"]))
    low, high = ok[: args.low], ok[-args.high:] if args.high else []
    picked = low + [r for r in high if r not in low]
    random.Random(7).shuffle(picked)  # so the labeler cannot infer confidence from order
    # Hide every Jev answer, not just this question's, so the check stays blind.
    answer_cols = {c[: -len("_confidence")] for c in header if c.endswith("_confidence")}
    out = args.out or re.sub(r"\.csv$", "", args.results) + f".review-{q}.csv"
    with open(out, "w", newline="") as f:
        cols = [c for c in header if c not in answer_cols and not c.endswith(("_confidence", "_route"))
                and c != "jev_error"]
        w = csv.DictWriter(f, fieldnames=["row_id"] + cols + ["human_label"], extrasaction="ignore")
        w.writeheader()
        for r in picked:
            w.writerow({"row_id": rows.index(r), **{c: r[c] for c in cols}, "human_label": ""})
    print(f"wrote {len(picked)} rows to {out}. Fill human_label WITHOUT looking at Jev's answers, then run calibrate.")


def same(answer, label):
    """Compare Jev's stored answer with a human label. A score answer (e.g. 2.71) is rounded to
    the nearest level number; a noul answer (yes probability) counts as yes at 0.5 or above."""
    try:
        value = float(answer)
    except ValueError:
        return answer == label
    if label.lower() in ("yes", "no", "true", "false"):
        return (value >= 0.5) == (label.lower() in ("yes", "true"))
    try:
        return round(value) == int(label)
    except ValueError:
        return False


def cmd_calibrate(args):
    sheet, _ = read_items(args.sheet)
    results, _ = read_items(args.results)
    q = args.question
    labeled = [(results[int(s["row_id"])], s["human_label"].strip()) for s in sheet if s.get("human_label", "").strip()]
    if not labeled:
        fail("no human_label values filled in yet")
    bands = [(1.0, 1.01), (0.9, 1.0), (0.75, 0.9), (0.0, 0.75)]
    print(f"{len(labeled)} labeled rows for '{q}'")
    best, still_passing = None, True
    for lo, hi in bands:  # highest band first; the cutoff is the lowest band before one misses
        hits = [same(r[q], lab) for r, lab in labeled if lo <= float(r[f"{q}_confidence"]) < hi]
        if hits:
            acc = sum(hits) / len(hits)
            label = "1.00" if lo == 1.0 else f"{lo:.2f}-{hi:.2f}"
            print(f"  confidence {label}: {sum(hits)}/{len(hits)} right ({acc:.0%})")
            if still_passing and acc >= args.target:
                best = lo
            else:
                still_passing = False
    wrong_confident = [(r[q], lab) for r, lab in labeled if float(r[f"{q}_confidence"]) >= 0.9 and not same(r[q], lab)]
    if wrong_confident:
        print(f"STOP: {len(wrong_confident)} confident answer(s) were wrong, e.g. Jev said {wrong_confident[0][0]!r}, "
              f"human said {wrong_confident[0][1]!r}. Fix the options before trusting any cutoff.")
    elif best is not None:
        print(f"Suggested cutoff: {best:.2f} (lowest band at or above {args.target:.0%} right). "
              f"Twenty rows is a sanity check, not proof; keep logging.")
    else:
        print("No band reached the target. Tighten the options or keep everything in review.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate"); v.add_argument("job"); v.add_argument("--items")
    r = sub.add_parser("run"); r.add_argument("job"); r.add_argument("items")
    r.add_argument("--limit", type=int, help="only the first N rows (20 for the sample run)")
    r.add_argument("--shuffle", action="store_true", help="with --limit, take a random sample instead")
    r.add_argument("--out"); r.add_argument("--model"); r.add_argument("--provider", choices=list(ENDPOINTS))
    r.add_argument("--concurrency", type=int, default=8, help="parallel requests")
    r.add_argument("--max-cost", type=float, default=1.0, help="refuse to start above this estimate, USD")
    rv = sub.add_parser("review"); rv.add_argument("results"); rv.add_argument("--question", required=True)
    rv.add_argument("--low", type=int, default=14); rv.add_argument("--high", type=int, default=6); rv.add_argument("--out")
    c = sub.add_parser("calibrate"); c.add_argument("sheet"); c.add_argument("results")
    c.add_argument("--question", required=True)
    c.add_argument("--target", type=float, default=0.95, help="accuracy a band must reach to act without review")
    args = p.parse_args()
    sys.exit({"validate": cmd_validate, "run": cmd_run, "review": cmd_review, "calibrate": cmd_calibrate}[args.cmd](args) or 0)


if __name__ == "__main__":
    main()
