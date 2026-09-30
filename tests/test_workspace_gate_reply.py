"""Round exports and team-guard gate reply.

Each round's comments are saved under reviews/ because Crit deletes a review once it is
approved. An export is written whole, never shrinks, and never touches an earlier round.
Replies go to Crit exactly as written, only while the human isn't mid-round and the session's
daemon is live, and nothing the tool does resolves a thread.
"""

import json
import os
import random
import unittest

from wsgate import GateCase, verdict_line, export_name, LineReader, wait_until

C1 = [{"id": "c1", "body": "why this?", "replies": [{"body": "because"}]}, {"id": "c2", "body": "ok", "resolved": True}]
C2 = C1 + [{"id": "c3", "body": "and this?"}]


class TestExports(GateCase):

    def test_each_round_is_exported_whole_to_reviews(self):
        self.ok(self.review("design", "plan.md", comments=C1))
        self.assertEqual(self.export("design", 1), C1)
        reply = self.sb.tmp / "reply.json"
        reply.write_text(json.dumps([{"reply_to": "c1", "body": "fixed"}]))
        with_reply = [dict(C1[0], replies=C1[0]["replies"] + [{"body": "fixed"}]), C1[1]]
        self.set_comments(with_reply)
        self.ok(self.gate("reply", "design", "--file", str(reply), "--author", "architect"))
        self.assertEqual(self.export("design", 1), with_reply, "reply re-exports the round")

        reviews = self.ws / "reviews"
        before = sorted(os.listdir(reviews))
        os.chmod(reviews, 0o555)
        try:
            self.review("design", comments=C2)
        finally:
            os.chmod(reviews, 0o755)
        self.assertEqual(sorted(os.listdir(reviews)), before, "a failed write leaves no new or partial file")
        self.assertEqual(self.export("design", 1), with_reply)

    def test_missing_data_exports_empty_and_exports_never_lose_comments(self):
        r = self.ok(self.review("design", "plan.md", comments=None))
        self.assertEqual(self.export("design", 1), [], "no data exports as an empty array")
        self.assertEqual(r.out.splitlines()[-1], verdict_line("design", 1, False, 0))

        self.ok(self.review("design", comments=C2))
        self.assertEqual(self.export("design", 1), C2, "a richer export replaces an emptier one")
        r = self.ok(self.review("design", comments=C1))
        self.assertIn("team-guard: warning: kept reviews/%s (the new export had fewer comments)" % export_name("design", 2),
                      r.err.splitlines(), self.show(r))
        self.assertEqual(self.export("design", 2), C2)
        self.assertEqual(self.export("design", 3), C1)
        earlier = {n: (self.ws / "reviews" / ("design-round%d.json" % n)).read_bytes() for n in (1, 2)}

        # Approval deletes Crit's data between the export at start and the one at the end.
        hold = self.sb.tmp / "hold"
        self.addCleanup(lambda: hold.exists() or hold.write_text("go"))
        self.set_comments(C1)
        p = self.popen_gate("review", "design", env={"FAKE_CRIT_HOLD": str(hold), "FAKE_CRIT_VERDICT": "true"})
        self.assertTrue(LineReader(p.stderr).wait_for("(session "))
        self.assertTrue(wait_until(lambda: self.export("design", 4) == C1))
        self.set_comments(None)
        hold.write_text("go")
        p.communicate(timeout=30)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self.export("design", 4), C1, "the approved round keeps its comments")
        earlier[3] = (self.ws / "reviews" / export_name("design", 3)).read_bytes()
        earlier[4] = (self.ws / "reviews" / export_name("design", 4)).read_bytes()

        self.ok(self.review("design", "plan.md", comments=[]))
        self.ok(self.review("design", comments=C2))
        for n, data in earlier.items():
            with self.subTest(round=n):
                self.assertEqual((self.ws / "reviews" / ("design-round%d.json" % n)).read_bytes(), data)


    def test_export_falls_back_to_the_saved_review_when_the_daemon_is_gone(self):
        self.ok(self.review("design", "plan.md", comments=C1))
        sid = (self.state() or {}).get("session")
        self.assertIsNotNone(sid, "the gate state holds the session")
        self.end_daemon(sid)
        self.set_comments(C2)
        n_before = len(self.calls())
        self.ok(self.review("design", comments=None))
        new = self.calls()[n_before:]
        fallback = ["comments", "-o", self.O, "--json", "--all", "%s/reviews/%s/review.json" % (self.O, sid)]
        self.assertEqual(new[0]["argv"], ["comments", "--session", sid, "--json", "--all"], "the live form first")
        self.assertEqual(new[1]["argv"], fallback, "then the saved review")
        self.assertEqual(self.export("design", 1), C1)


class TestReply(GateCase):

    def test_reply_posts_the_file_unchanged_and_re_exports(self):
        self.ok(self.review("design", "plan.md", comments=C1))
        reply = self.sb.tmp / "replies.json"
        body = 'line one\nline "two" — café ✓'
        reply.write_bytes(json.dumps([{"reply_to": "c1", "body": body, "file": "plan.md", "author": "product-owner"},
                                      {"reply_to": "c2", "body": "noted", "resolve": False}],
                                     ensure_ascii=False, indent=1).encode("utf-8"))
        n_before = len(self.calls())
        self.set_comments(C2)
        r = self.ok(self.gate("reply", "design", "--file", str(reply), "--author", "architect"))
        new = self.calls()[n_before:]
        sid = self.state()["session"]
        self.assertEqual(new[0]["argv"], ["comment", "--session", sid, "--json", "--author", "architect"])
        self.assertEqual(new[0]["stdin"], reply.read_bytes().decode("utf-8"), "the file's bytes go to Crit on stdin")
        self.assertEqual(new[1]["argv"][0], "comments", "the round is exported after the replies")
        self.assertEqual(self.reply_copy.read_bytes(), reply.read_bytes())
        self.assertEqual(self.export("design", 1), C2)

        r = self.code(self.gate("reply", "design", "--file", str(reply), "--author", "architect",
                                env={"FAKE_CRIT_REPLY_EXIT": "4"}), 1)
        self.assertEqual(self.calls()[-1]["argv"][0], "comments", "the export is attempted even when Crit fails")

    def test_reply_waits_for_the_round_and_needs_a_live_session(self):
        good = self.sb.tmp / "good.json"
        good.write_text(json.dumps([{"reply_to": "c1", "body": "x"}]))
        hold = self.sb.tmp / "hold"
        self.addCleanup(lambda: hold.exists() or hold.write_text("go"))
        self.set_comments(C1)
        p = self.popen_gate("review", "design", "plan.md", env={"FAKE_CRIT_HOLD": str(hold)})
        self.assertTrue(LineReader(p.stderr).wait_for("(session "))
        self.assertTrue(wait_until(lambda: (self.state() or {}).get("pending") is not None))
        r = self.code(self.gate("reply", "design", "--file", str(good), "--author", "architect"), 1)
        self.assertFalse([c for c in self.calls() if c["argv"][:1] == ["comment"]], "no reply mid-round")
        hold.write_text("go")
        p.communicate(timeout=30)

        sid = (self.state() or {}).get("session")
        self.assertIsNotNone(sid, "the gate state holds the session")
        self.end_daemon(sid)
        r = self.code(self.gate("reply", "design", "--file", str(good), "--author", "architect"), 1)
        self.has(r.err, "gate review", r)
        r = self.code(self.gate("reply", "design", "--file", str(good), "--author", "architect",
                                env={"CRIT_BIN": str(self.sb.tmp / "no-crit")}), 1)
        self.assertIn('team-guard: gate design: Crit is unavailable; use the chat fallback: the reply that approves '
                      'this gate is exactly "approve design"', r.err.splitlines())

    def test_invalid_replies_never_reach_crit(self):
        self.ok(self.review("design", "plan.md", comments=C1))
        n_calls = len(self.calls())
        cases = {"not an array": {"reply_to": "c1", "body": "x"}, "entry not an object": ["x"],
                 "no reply_to": [{"body": "x"}], "reply_to not a string": [{"reply_to": 3, "body": "x"}],
                 "empty body": [{"reply_to": "c1", "body": ""}], "no body": [{"reply_to": "c1"}],
                 "resolve true": [{"reply_to": "c1", "body": "x", "resolve": True}],
                 "resolve string": [{"reply_to": "c1", "body": "x", "resolve": "yes"}]}
        for name, data in cases.items():
            with self.subTest(case=name):
                f = self.sb.tmp / ("bad-%d.json" % len(name))
                f.write_text(json.dumps(data))
                r = self.code(self.gate("reply", "design", "--file", str(f), "--author", "architect"), 1)
                self.no_traceback(r)
        good = self.sb.tmp / "good.json"
        good.write_text(json.dumps([{"reply_to": "c1", "body": "x"}]))
        bad_json = self.sb.tmp / "bad.json"
        bad_json.write_text("{not json")
        for name, args in {"missing file": ("--file", str(self.sb.tmp / "nope.json"), "--author", "a"),
                           "invalid JSON": ("--file", str(bad_json), "--author", "a"),
                           "no author": ("--file", str(good)), "empty author": ("--file", str(good), "--author", ""),
                           "resolve flag": ("--file", str(good), "--author", "a", "--resolve")}.items():
            with self.subTest(case=name):
                if "--resolve" in args:
                    self.nonzero(self.gate("reply", "design", *args))
                else:
                    self.code(self.gate("reply", "design", *args), 1)
        self.code(self.gate("reply", "never-reviewed", "--file", str(good), "--author", "a"), 1)
        self.assertEqual(len(self.calls()), n_calls, "no Crit process ran")

    def test_no_reply_ever_resolves_a_thread_property(self):
        self.ok(self.review("design", "plan.md", comments=C1))
        rng = random.Random(13)
        for i in range(10):
            entries = []
            for j in range(rng.randint(1, 3)):
                e = {"reply_to": "c%d" % rng.randint(1, 3), "body": "reply %d.%d" % (i, j)}
                if rng.random() < 0.3:
                    e["resolve"] = rng.choice([False, True, 1, "true"])
                entries.append(e)
            f = self.sb.tmp / ("p%d.json" % i)
            f.write_text(json.dumps(entries))
            self.reply_copy.unlink(missing_ok=True)
            self.gate("reply", "design", "--file", str(f), "--author", "architect")
            if self.reply_copy.exists():
                for e in json.loads(self.reply_copy.read_text()):
                    self.assertIn(e.get("resolve", False), (False,), e)
        for c in self.calls():
            self.assertNotIn("--resolve", c["argv"])


if __name__ == "__main__":
    unittest.main()
