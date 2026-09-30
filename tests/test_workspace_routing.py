"""team-guard routing record / summary: the append-only routing log and its summary.

Recording what the lead proposed, what the human confirmed and how long each step took
is what lets the routing rules be evaluated later, so the log must only ever grow and
every line must stay a complete JSON object.
"""

import json
import random
import threading
import unittest

from wslib import tree_snapshot
from wstools import ToolCase, ROUTING_MD, ANSWER_KEYS, TS_RE, VARIANTS, route_event


class RoutingCase(ToolCase):

    def record(self, *args, cwd=None, env=None):
        return self.sb.tg("routing", "record", *args, cwd=cwd, env=env)

    def summary(self, *args, cwd=None):
        return self.sb.tg("routing", "summary", *args, cwd=cwd or self.sb.scratch)

    def routing_md_lines(self, where):
        p = where / ROUTING_MD
        return p.read_text().splitlines() if p.exists() else None


class TestRecordRoute(RoutingCase):

    def test_route_is_appended_to_the_log_and_the_workspace_notes(self):
        A = self.sb.new_repo("A")
        ws = self.new_ws("f")
        wt = self.attach(A)
        wt_before = tree_snapshot(wt)
        r = self.ok(self.record("route", "--proposed", "standard", "--confirmed", "standard",
                                "--answer", "repos=1", "--answer", "contract=no", "--answer", "files=<=3", cwd=ws))
        self.assertEqual(r.out, "team-guard: recorded route for f\n")
        lines = self.log_lines()
        self.assertEqual(len(lines), 1, self.log_path.read_text())
        e = lines[0]
        self.assertEqual(e["v"], 1)
        self.assertRegex(e["ts"], TS_RE)
        self.assertEqual((e["event"], e["feature"], e["proposed"], e["confirmed"]), ("route", "f", "standard", "standard"))
        self.assertNotIn("override_reason", e)
        self.assertEqual(sorted(e["answers"]), sorted(ANSWER_KEYS))
        self.assertEqual(str(e["answers"]["repos"]), "1")
        self.assertEqual(e["answers"]["contract"], "no")
        self.assertEqual(e["answers"]["files"], "<=3")
        for k in set(ANSWER_KEYS) - {"repos", "contract", "files"}:
            self.assertEqual(e["answers"][k], "unknown", k)
        self.assertTrue(self.log_path.read_text().endswith("\n"))
        md = self.routing_md_lines(ws)
        self.assertEqual(len(md), 1, md)
        self.assertTrue(md[0].startswith("- %s route f:" % e["ts"]), md)
        self.assertIn("standard", md[0])
        self.assertEqual(tree_snapshot(wt), wt_before, "the attached worktree is untouched")
        self.assertEqual(self.sb.git(wt, "status", "--porcelain", "--ignored"), "")

        # With an override reason, confirmed may differ from proposed and the reason is kept.
        self.ok(self.record("route", "--proposed", "bug-fix", "--confirmed", "full", "--override-reason",
                            "touches stored data", cwd=ws))
        e2 = self.log_lines()[-1]
        self.assertEqual((e2["proposed"], e2["confirmed"], e2["override_reason"]), ("bug-fix", "full", "touches stored data"))

        # Outside a workspace: the notes go into the repo's git dir, or nowhere without a repo.
        R = self.feature_repo("R")
        before = tree_snapshot(R, skip=(".git",))
        self.ok(self.record("route", "--feature", "g", "--proposed", "chore", "--confirmed", "chore", cwd=R))
        self.assertEqual(len(self.routing_md_lines(self.git_dir(R))), 1)
        self.assertIsNone(self.routing_md_lines(R))
        self.assertEqual(tree_snapshot(R, skip=(".git",)), before)
        scratch_before = tree_snapshot(self.sb.scratch)
        self.ok(self.record("route", "--feature", "h", "--proposed", "spike", "--confirmed", "spike"))
        self.assertEqual(tree_snapshot(self.sb.scratch), scratch_before)
        self.assertEqual([x["feature"] for x in self.log_lines()], ["f", "f", "g", "h"])

    def test_invalid_route_input_writes_nothing(self):
        ws = self.new_ws("f")
        cases = {
            "unknown variant": ("--proposed", "medium", "--confirmed", "medium"),
            "unknown answer key": ("--proposed", "chore", "--confirmed", "chore", "--answer", "colour=red"),
            "bad answer value": ("--proposed", "chore", "--confirmed", "chore", "--answer", "security=maybe"),
            "bad repos value": ("--proposed", "chore", "--confirmed", "chore", "--answer", "repos=0"),
            "bad files value": ("--proposed", "chore", "--confirmed", "chore", "--answer", "files=7"),
            "override without reason": ("--proposed", "chore", "--confirmed", "full"),
            "bad feature name": ("--feature", "../x", "--proposed", "chore", "--confirmed", "chore"),
        }
        for name, args in cases.items():
            with self.subTest(case=name):
                r = self.code(self.record("route", *args, cwd=ws), 1)
                self.assertTrue(r.err.strip(), "a reason is printed")
                self.no_traceback(r)
                self.assertFalse(self.log_path.exists(), "no log written")
                self.assertIsNone(self.routing_md_lines(ws))
        r = self.code(self.record("route", "--proposed", "chore", "--confirmed", "chore"), 1)
        self.has(r.err, "--feature", r)
        self.assertFalse(self.log_path.exists())


class TestRecordStepAndUpgrade(RoutingCase):

    def test_steps_and_upgrades_append_and_a_variant_never_goes_down(self):
        ws = self.new_ws("f")
        self.ok(self.record("route", "--proposed", "standard", "--confirmed", "standard", cwd=ws))
        r = self.ok(self.record("step", "--step", "build", "--seconds", "120", cwd=ws))
        self.assertEqual(r.out, "team-guard: recorded step for f\n")
        r = self.ok(self.record("upgrade", "--to", "full", "--reason", "a second repo is needed", cwd=ws))
        self.assertEqual(r.out, "team-guard: recorded upgrade for f\n")
        route, step, up = self.log_lines()
        self.assertEqual((step["event"], step["step"], step["seconds"]), ("step", "build", 120))
        self.assertIsInstance(step["seconds"], int)
        self.assertEqual((up["event"], up["from"], up["to"], up["reason"]),
                         ("upgrade", "standard", "full", "a second repo is needed"))
        for e in (step, up):
            self.assertEqual((e["v"], e["feature"]), (1, "f"))
            self.assertRegex(e["ts"], TS_RE)
        md = self.routing_md_lines(ws)
        self.assertEqual(len(md), 3)
        self.assertTrue(md[1].startswith("- %s step f:" % step["ts"]) and "build" in md[1] and "120" in md[1], md)
        self.assertTrue(md[2].startswith("- %s upgrade f:" % up["ts"]) and "full" in md[2], md)

        before = self.log_path.read_bytes()
        refused = [("upgrade", "--to", "standard", "--reason", "smaller than thought"),
                   ("upgrade", "--to", "full", "--reason", "same again"),
                   ("upgrade", "--to", "spike", "--reason", "explore"),
                   ("step", "--step", "Bad Slug", "--seconds", "3"),
                   ("step", "--step", "build", "--seconds", "-1")]
        for args in refused:
            with self.subTest(args=args):
                self.code(self.record(*args, cwd=ws), 1)
                self.assertEqual(self.log_path.read_bytes(), before)
        self.code(self.record("upgrade", "--feature", "never-routed", "--to", "full", "--reason", "x"), 1)
        self.ok(self.record("route", "--feature", "s", "--proposed", "spike", "--confirmed", "spike"))
        self.code(self.record("upgrade", "--feature", "s", "--to", "full", "--reason", "ship it"), 1)
        self.assertEqual(len(self.log_lines()), 4)

    def test_log_is_append_only_even_under_concurrent_records_property(self):
        seen = b""
        rng = random.Random(5)
        for i in range(6):
            v = rng.choice(VARIANTS)
            self.ok(self.record("route", "--feature", "p%d" % i, "--proposed", v, "--confirmed", v))
            now = self.log_path.read_bytes()
            self.assertTrue(now.startswith(seen), "earlier bytes are unchanged")
            seen = now
        errors = []

        def worker(tag):
            for n in range(50):
                r = self.record("step", "--feature", "p0", "--step", "%s-%d" % (tag, n), "--seconds", str(n))
                if r.code != 0:
                    errors.append(self.show(r))

        threads = [threading.Thread(target=worker, args=(t,)) for t in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        data = self.log_path.read_bytes()
        self.assertTrue(data.startswith(seen))
        lines = data.decode("utf-8").split("\n")
        self.assertEqual(lines[-1], "", "every line ends in a newline")
        objs = [json.loads(l) for l in lines[:-1]]
        self.assertEqual(len(objs), 6 + 100)
        self.assertEqual(sorted(o["step"] for o in objs if o["event"] == "step"),
                         sorted("%s-%d" % (t, n) for t in ("a", "b") for n in range(50)))


class TestSummary(RoutingCase):

    def fixture(self):
        self.write_log(
            route_event("f1", "standard", "standard"),
            {"v": 1, "ts": "2026-01-01T00:01:00Z", "event": "step", "feature": "f1", "step": "design", "seconds": 100},
            {"v": 1, "ts": "2026-01-01T00:02:00Z", "event": "step", "feature": "f1", "step": "build", "seconds": 50},
            route_event("f2", "bug-fix", "standard") | {"override_reason": "bigger"},
            {"v": 1, "ts": "2026-01-01T00:03:00Z", "event": "upgrade", "feature": "f2", "from": "standard",
             "to": "full", "reason": "second repo"},
            {"v": 1, "ts": "2026-01-01T00:04:00Z", "event": "step", "feature": "f2", "step": "build", "seconds": 30},
            route_event("f3", "full", "standard") | {"override_reason": "smaller"},
            route_event("f3", "chore", "chore", ts="2026-01-02T00:00:00Z"),
            {"v": 1, "ts": "2026-01-02T00:01:00Z", "event": "step", "feature": "f3", "step": "build", "seconds": 10},
            route_event("f4", "spike", "standard") | {"override_reason": "needs to ship"},
            {"v": 1, "ts": "2026-01-03T00:00:00Z", "event": "step", "feature": "f5", "step": "build", "seconds": 999},
            {"v": 1, "ts": "2026-01-03T00:00:00Z", "event": "vote", "feature": "f1"},
            raw=["not json", json.dumps({"v": 2, "event": "route", "feature": "f9"}), "[1, 2]"],
        )

    EXPECTED = {
        "runs": 4, "overrides": 2, "overrides_up": 1, "overrides_down": 0, "overrides_lateral": 1,
        "override_rate": 0.5, "upgraded_runs": 1, "upgrade_rate": 0.25,
        "time_by_variant": {"standard": {"runs": 2, "total_seconds": 150, "mean_seconds": 75.0},
                            "full": {"runs": 1, "total_seconds": 30, "mean_seconds": 30.0},
                            "chore": {"runs": 1, "total_seconds": 10, "mean_seconds": 10.0}},
        "malformed_lines": 3,
    }

    def test_summary_reports_overrides_upgrades_and_time_per_variant(self):
        self.fixture()
        before = self.log_path.read_bytes()
        r = self.ok(self.summary("--json"))
        got = json.loads(r.out)
        self.assertEqual(got, self.EXPECTED)
        self.assertEqual(self.log_path.read_bytes(), before, "summary never writes the log")

        text = self.ok(self.summary()).out.splitlines()
        flat = []
        for k, v in got.items():
            if isinstance(v, dict):
                for var, fields in v.items():
                    for f, x in fields.items():
                        flat.append(("%s.%s.%s" % (k, var, f), x))
            else:
                flat.append((k, v))
        self.assertEqual([l.split(": ", 1)[0] for l in text], [k for k, _ in flat])
        for line, (k, v) in zip(text, flat):
            with self.subTest(key=k):
                shown = line.split(": ", 1)[1]
                self.assertAlmostEqual(float(shown), float(v))

        self.log_path.unlink()
        empty = json.loads(self.ok(self.summary("--json")).out)
        self.assertEqual(empty, {"runs": 0, "overrides": 0, "overrides_up": 0, "overrides_down": 0,
                                 "overrides_lateral": 0, "override_rate": None, "upgraded_runs": 0,
                                 "upgrade_rate": None, "time_by_variant": {}, "malformed_lines": 0})
        text = self.ok(self.summary()).out
        self.assertIn("override_rate: n/a", text.splitlines())
        self.assertIn("upgrade_rate: n/a", text.splitlines())

    def test_summary_counts_stay_within_bounds_property(self):
        rng = random.Random(11)
        for it in range(5):
            with self.subTest(iteration=it):
                if self.log_path.exists():
                    self.log_path.unlink()
                events, routes = [], 0
                for n in range(rng.randint(0, 25)):
                    feat = "f%d" % rng.randint(0, 6)
                    kind = rng.choice(["route", "route", "step", "upgrade", "junk"])
                    if kind == "route":
                        routes += 1
                        events.append(json.dumps(route_event(feat, rng.choice(VARIANTS), rng.choice(VARIANTS))))
                    elif kind == "step":
                        events.append(json.dumps({"v": 1, "ts": "2026-01-01T00:00:00Z", "event": "step",
                                                  "feature": feat, "step": "s", "seconds": rng.randint(0, 500)}))
                    elif kind == "upgrade":
                        events.append(json.dumps({"v": 1, "ts": "2026-01-01T00:00:00Z", "event": "upgrade",
                                                  "feature": feat, "from": "chore", "to": "full", "reason": "r"}))
                    else:
                        events.append("{broken")
                self.write_log(raw=events)
                got = json.loads(self.ok(self.summary("--json")).out)
                self.assertLessEqual(got["runs"], routes)
                for k in ("override_rate", "upgrade_rate"):
                    if got["runs"] == 0:
                        self.assertIsNone(got[k])
                    else:
                        self.assertTrue(0 <= got[k] <= 1, got)
                self.assertEqual(got["malformed_lines"], sum(1 for e in events if e == "{broken"))


if __name__ == "__main__":
    unittest.main()
