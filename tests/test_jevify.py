"""Offline tests for jevify/scripts/jev.py. The API call is faked; nothing touches the network."""
import argparse
import csv
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "jevify", "scripts"))
import jev  # noqa: E402

JOB = {
    "name": "t",
    "fields": ["text"],
    "questions": {
        "topic": {"type": "choice", "instructions": "Pick.",
                  "criteria": {"yes": "a", "no": "b", "unclear": "c"}},
        "fit": {"type": "score", "instructions": "Rate.", "criteria": ["low", "mid", "high"]},
    },
    "cutoffs": {"topic": 0.9},
}


def fake_call(provider, model, state, questions, **_):
    yes = "price" in state["text"]
    return {
        "answers": {
            "topic": {"choice": "yes" if yes else "no", "confidence": 0.95 if yes else 0.6,
                      "probabilities": {"yes": 0.95 if yes else 0.2, "no": 0.05 if yes else 0.6,
                                        "unclear": 0.0 if yes else 0.2}},
            "fit": {"score": 1.8, "confidence": 0.7,
                    "probabilities": {"0": 0.1, "1": 0.2, "2": 0.7}},
        },
        "usage": {"cost": 0.00001},
    }


class JevTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.job = os.path.join(self.dir, "job.json")
        self.items = os.path.join(self.dir, "rows.csv")
        with open(self.job, "w") as f:
            json.dump(JOB, f)
        with open(self.items, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["text"])
            for t in ["the price is $49", "a founder story", "price went up", "holiday tips"]:
                w.writerow([t])

    def test_validate_flags_placeholder_and_missing_abstain(self):
        job = json.loads(json.dumps(JOB))
        job["shared_state"] = {"brand": "REPLACE with your brand"}
        job["questions"]["topic"]["criteria"] = {"yes": "a", "no": "b"}
        errors, warnings = jev.check_job(job, ["text"])
        self.assertTrue(any("REPLACE" in e for e in errors))
        self.assertTrue(any("unclear" in w for w in warnings))

    def test_run_writes_every_row(self):
        args = argparse.Namespace(job=self.job, items=self.items, limit=None, shuffle=False, out=None,
                                  model=None, provider=None, concurrency=2, max_cost=1.0)
        with mock.patch.object(jev, "call", fake_call), \
                mock.patch.object(jev, "pick_provider", lambda _: "openrouter"), \
                redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
            jev.cmd_run(args)
        summary = json.loads(out.getvalue())
        with open(summary["out"], newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 4)
        self.assertEqual([r["topic"] for r in rows], ["yes", "no", "yes", "no"])
        self.assertEqual([r["topic_route"] for r in rows], ["auto", "review", "auto", "review"])
        self.assertEqual(summary["answers"]["topic"], {"yes": 2, "no": 2})
        self.assertIn("mean", summary["answers"]["fit"])

    def test_calibrate_matches_score_and_noul_labels(self):
        self.assertTrue(jev.same("1.8", "2"))
        self.assertFalse(jev.same("1.4", "2"))
        self.assertTrue(jev.same("0.8", "yes"))
        self.assertTrue(jev.same("0.2", "no"))
        self.assertTrue(jev.same("buy", "buy"))
        self.assertFalse(jev.same("buy", "learn"))

    def test_shipped_jobs_are_well_formed(self):
        jobs_dir = str(jev.JOBS_DIR)
        for name in sorted(os.listdir(jobs_dir)):
            job = jev.load_job(os.path.join(jobs_dir, name))
            errors, _ = jev.check_job(job)
            real = [e for e in errors if "REPLACE" not in e]
            self.assertEqual(real, [], name)

    def test_openrouter_is_chosen_first_when_both_keys_are_set(self):
        both = {"OPENROUTER_API_KEY": "a", "TYPESAFE_API_KEY": "b"}
        with mock.patch.dict(os.environ, both):
            self.assertEqual(jev.pick_provider(None), "openrouter")
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "", "TYPESAFE_API_KEY": "b"}):
            self.assertEqual(jev.pick_provider(None), "typesafe")

    def test_request_asks_for_the_latest_jev(self):
        sent = {}

        class Reply:
            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, timeout=None):
            sent.update(json.loads(request.data))
            return Reply()
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-" "or-v1-fakekey"}), \
                mock.patch.object(jev.urllib.request, "urlopen", fake_urlopen):
            jev.call("openrouter", jev.ENDPOINTS["openrouter"][2], {"text": "x"}, JOB["questions"])
        self.assertEqual(sent["model"], "~typesafe/jev-latest")

    def test_key_never_appears_in_errors(self):
        key = "sk-" "or-v1-fakekey0123"
        error = jev.urllib.error.HTTPError("u", 401, "no", {}, io.BytesIO(
            json.dumps({"error": {"message": "bad key " + key}}).encode()))
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": key}), \
                mock.patch.object(jev.urllib.request, "urlopen", mock.Mock(side_effect=error)):
            with self.assertRaises(RuntimeError) as caught:
                jev.call("openrouter", "m", {}, JOB["questions"], retries=0)
            self.assertNotIn(key, str(caught.exception))
            with self.assertRaises(SystemExit) as stopped:
                jev.fail("oops " + key)
            self.assertNotIn(key, str(stopped.exception))

    def test_ready_job_found_by_name_in_assets(self):
        job = jev.load_job("paid-search-term-intent")
        self.assertIn("questions", job)
        self.assertTrue(str(jev.JOBS_DIR).endswith(os.path.join("assets", "jobs")))


if __name__ == "__main__":
    unittest.main()
