"""team-guard gate review: run a local Crit review round for a design gate and keep its data.

Approval deletes Crit's review data, so every round is exported as it runs. The review is
started detached, so a killed wait loses nothing and a re-run resumes the same round.
"""

import json
import os
import random
import signal
import unittest

from wsgate import GateCase, verdict_line, export_name, LineReader, FORBIDDEN, sha, wait_until
from wstools import pid_alive

C1 = [{"id": "c1", "body": "why this?", "replies": [{"body": "because"}]}, {"id": "c2", "body": "ok", "resolved": True}]
C2 = C1 + [{"id": "c3", "body": "and this?"}]
ENV_WANT = {"CRIT_NO_UPDATE_CHECK": "1", "CRIT_PLAN_REVIEW": "off", "CRIT_SHARE_URL": ""}


def starts(calls):
    return [c for c in calls if c["argv"][:1] not in (["comments"], ["comment"])]


class TestReviewStart(GateCase):

    def test_new_review_runs_crit_locally_and_streams_its_output(self):
        hold = self.sb.tmp / "hold"
        self.set_comments(C1)
        p = self.popen_gate("review", "design", "plan.md", "notes.md", env={"FAKE_CRIT_HOLD": str(hold)})
        out = LineReader(p.stdout)
        try:
            seen = out.wait_for("(session s1)")
        finally:
            hold.write_text("go")
        err = p.stderr.read()
        p.wait(timeout=30)
        out.wait_for("\x00never", timeout=5)
        self.assertTrue(seen, "the session line reached the caller while Crit was still running")
        self.assertEqual(p.returncode, 0, err)
        self.assertIn("Started crit daemon at http://localhost:47111 (session s1)", out.lines)
        self.assertIn("approved: false", err.splitlines())
        start = starts(self.calls())
        self.assertEqual(len(start), 1)
        self.assertEqual(start[0]["argv"], ["-o", self.O, "plan.md", "notes.md"])
        self.assertEqual(os.path.realpath(start[0]["cwd"]), str(self.ws))
        for c in self.calls():
            with self.subTest(argv=c["argv"]):
                self.assertEqual(c["env"], ENV_WANT)

        self.ok(self.review("other", "plan.md", "--no-open"))
        self.assertEqual(starts(self.calls())[-1]["argv"], ["-o", self.O, "--no-open", "plan.md"])

    def test_bad_review_input_runs_nothing_and_writes_nothing(self):
        outside = self.sb.tmp / "outside.md"
        outside.write_text("x\n")
        os.symlink(outside, self.ws / "link.md")
        (self.ws / "adir").mkdir()
        before = self.ws_snapshot()
        cases = {"no file": ("design",), "missing file": ("design", "nope.md"), "directory": ("design", "adir"),
                 "symlink out of the workspace": ("design", "link.md"), "bad gate name": ("../x", "plan.md"),
                 "leading dash": ("-x", "plan.md"), "absolute outside": ("design", str(outside))}
        for name, args in cases.items():
            with self.subTest(case=name):
                r = self.code(self.gate("review", *args), 1)
                self.no_traceback(r)
                self.assertEqual(self.calls(), [])
                self.assertEqual(self.ws_snapshot(), before)
        r = self.code(self.gate("review", "design", "plan.md", cwd=self.sb.scratch), 1)
        self.assertEqual(self.calls(), [])

        rng = random.Random(21)
        for _ in range(8):
            bad = rng.choice(["", ".", "-", "_"]) + "".join(rng.choice("ab/ :*") for _ in range(rng.randint(1, 4)))
            if not bad or bad[0] in "._-" or any(ch in bad for ch in "/ :*"):
                with self.subTest(name=bad):
                    self.code(self.gate("review", bad, "plan.md"), 1)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.ws_snapshot(), before)

    def test_unavailable_crit_exits_three_and_names_the_chat_reply(self):
        noexec = self.sb.tmp / "not-executable"
        noexec.write_text("#!/bin/sh\nexit 0\n")
        noexec.chmod(0o644)
        before = self.ws_snapshot()
        for binpath in (self.sb.tmp / "missing-crit", noexec):
            for gate, literal in (("design", "approve design"), ("D1", "approve D1"), ("D2", "approve D2"),
                                  ("alpha", "approve design")):
                with self.subTest(bin=binpath.name, gate=gate):
                    r = self.code(self.gate("review", gate, "plan.md", env={"CRIT_BIN": str(binpath)}), 3)
                    self.assertIn('team-guard: gate %s: Crit is unavailable; use the chat fallback: the reply that '
                                  'approves this gate is exactly "%s"' % (gate, literal), r.err.splitlines())
                    self.assertEqual(self.ws_snapshot(), before)


class TestReviewRounds(GateCase):

    def test_unfinished_review_continues_its_session_as_a_new_round(self):
        self.ok(self.review("design", "plan.md", "notes.md", comments=C1))
        self.assertEqual(self.export("design", 1), C1)
        n_before = len(self.calls())
        self.set_comments(C2)
        r = self.ok(self.review("design", comments=None))
        new = self.calls()[n_before:]
        kinds = [c["argv"][0] for c in new]
        self.assertEqual(new[0]["argv"], ["comments", "-o", self.O, "--session", "s1", "--json", "--all"],
                         "the outgoing round is exported first")
        cont = starts(new)
        self.assertEqual([c["argv"] for c in cont], [["-o", self.O, "--session", "s1"]], kinds)
        self.assertEqual(r.out.splitlines()[-1], verdict_line("design", 2, False, 2))
        self.assertEqual(self.export("design", 2), C2)
        self.assertEqual(self.export("design", 1), C2, "the outgoing round was re-exported with the replies")

        r = self.code(self.review("design", "plan.md", comments=None), 1)
        self.has(r.err, "--new", r)
        self.ok(self.review("design", "notes.md", "plan.md", comments=None))
        self.assertEqual(self.state()["round"], 3)

        kept = {n: (self.ws / "reviews" / ("design-round%d.json" % n)).read_bytes() for n in (1, 2, 3)}
        n_before = len(self.calls())
        self.ok(self.review("design", "--new", "plan.md", comments=C1, session="s2"))
        new = self.calls()[n_before:]
        self.assertEqual(new[0]["argv"][:4], ["comments", "-o", self.O, "--session"])
        self.assertEqual(starts(new)[0]["argv"], ["-o", self.O, "plan.md"])
        st = self.state()
        self.assertEqual((st["round"], st["open_round"], st["files"]), (4, 4, ["plan.md"]))
        for n in (1, 2):
            self.assertEqual((self.ws / "reviews" / ("design-round%d.json" % n)).read_bytes(), kept[n])

        self.ok(self.review("design", comments=None, verdict="true", session="s2"))
        n = self.state()["round"]
        self.ok(self.review("design", "notes.md", comments=[], session="s3"))
        st = self.state()
        self.assertEqual((st["round"], st["open_round"]), (n + 1, n + 1), "a reopen opens a fresh review")
        self.assertEqual(starts(self.calls())[-1]["argv"], ["-o", self.O, "notes.md"])
        for m in range(1, n + 1):
            self.assertTrue((self.ws / "reviews" / ("design-round%d.json" % m)).exists(), m)

    def test_verdict_is_reported_stored_and_approval_hashes_the_files(self):
        r = self.ok(self.review("design", "plan.md", "notes.md", comments=C2))
        self.assertEqual(r.out.splitlines()[-1], verdict_line("design", 1, False, 2))
        st = self.state()
        self.assertEqual((st["approved"], st["session"], st["round"]), (False, "s1", 1))
        self.assertIsNone(st.get("pending"))
        r = self.ok(self.review("design", comments=C1, verdict="true"))
        self.assertEqual(r.out.splitlines()[-1], verdict_line("design", 2, True, 1))
        st = self.state()
        self.assertTrue(st["approved"])
        self.assertEqual(st["hashes"], {"plan.md": sha(self.ws / "plan.md"), "notes.md": sha(self.ws / "notes.md")})

        for label, env in (("non-zero exit", {"FAKE_CRIT_EXIT": "2"}), ("no verdict", {"FAKE_CRIT_VERDICT": "none"})):
            with self.subTest(case=label):
                self.ok(self.review("g%d" % len(label), "plan.md", comments=C1))
                gate = "g%d" % len(label)
                before = self.state(gate)
                n_calls = len(self.calls())
                self.set_comments(C2)
                e = {"FAKE_CRIT_VERDICT": "false"}
                e.update(env)
                r = self.code(self.gate("review", gate, env=e), 1)
                after = self.state(gate)
                for k in ("round", "approved", "session", "hashes", "open_round"):
                    self.assertEqual(after.get(k), before.get(k), k)
                self.assertIn("comments", [c["argv"][0] for c in self.calls()[n_calls:]][1:],
                              "the round is still exported after Crit fails")
                self.assertEqual(self.export(gate, 2), C2)


class TestKilledWait(GateCase):

    def test_review_is_detached_exported_at_start_and_resumed_after_a_kill(self):
        hold = self.sb.tmp / "hold"
        self.set_comments(C1)
        p = self.popen_gate("review", "design", "plan.md", env={"FAKE_CRIT_HOLD": str(hold)})
        self.addCleanup(lambda: hold.exists() or hold.write_text("go"))
        out = LineReader(p.stdout)
        self.assertTrue(out.wait_for("(session s1)"), "the review started")
        self.assertTrue(wait_until(lambda: self.export("design", 1) == C1), "the first round is exported while Crit runs")
        st = self.state()
        self.assertEqual(st["pending"], {"round": 1, "session": "s1"})

        fake = starts(self.calls())[0]
        self.assertNotEqual(fake["pgid"], os.getpgid(p.pid), "Crit runs in its own process group")
        self.assertNotEqual(fake["sid"], os.getsid(p.pid), "and its own session")
        os.killpg(p.pid, signal.SIGKILL)
        p.wait(timeout=30)
        self.assertTrue(pid_alive(fake["pid"]), "Crit survives the killed wait")
        self.assertEqual(self.export("design", 1), C1, "the round's comments are kept")

        hold.write_text("go")
        self.set_comments(C2)
        n_before = len(self.calls())
        r = self.ok(self.gate("review", "design", env={"FAKE_CRIT_HOLD": str(hold)}))
        cont = starts(self.calls()[n_before:])
        self.assertEqual([c["argv"] for c in cont], [["-o", self.O, "--session", "s1"]])
        self.assertEqual(r.out.splitlines()[-1], verdict_line("design", 1, False, 2))
        st = self.state()
        self.assertIsNone(st.get("pending"))
        self.assertEqual(st["round"], 1)

    def test_new_after_a_killed_wait_discards_the_pending_round(self):
        hold = self.sb.tmp / "hold"
        self.set_comments(C1)
        p = self.popen_gate("review", "design", "plan.md", env={"FAKE_CRIT_HOLD": str(hold)})
        self.addCleanup(lambda: hold.exists() or hold.write_text("go"))
        self.assertTrue(LineReader(p.stdout).wait_for("(session s1)"))
        self.assertTrue(wait_until(lambda: (self.state() or {}).get("pending") is not None))
        os.killpg(p.pid, signal.SIGKILL)
        p.wait(timeout=30)
        hold.write_text("go")
        n_before = len(self.calls())
        self.ok(self.review("design", "--new", "plan.md", comments=C1, session="s9"))
        self.assertEqual(starts(self.calls()[n_before:])[0]["argv"], ["-o", self.O, "plan.md"])
        self.assertIsNone(self.state().get("pending"))


class TestOffline(GateCase):

    def test_gate_commands_only_ever_run_the_configured_crit(self):
        self.ok(self.review("design", "plan.md", comments=C1))
        f = self.sb.tmp / "reply.json"
        f.write_text(json.dumps([{"reply_to": "c1", "body": "done"}]))
        self.ok(self.gate("reply", "design", "--file", str(f), "--author", "architect"))
        self.ok(self.review("design", verdict="true", comments=[]))
        self.ok(self.gate("record", "design", "--crit"))
        self.ok(self.gate("check", "design"))
        self.ok(self.gate("record", "D1", "plan.md", "--chat", "approve D1"))
        self.assertTrue(self.calls(), "the fake named by CRIT_BIN did the work")
        self.assertFalse(self.guard_log.exists(), "the crit on PATH was never run")
        self.assertTrue(all(os.path.realpath(c["cwd"]) == str(self.ws) for c in self.calls()))


class TestReviewInvariants(GateCase):

    def test_crit_is_only_ever_run_locally_property(self):
        rng = random.Random(4)
        self.ok(self.review("design", "plan.md", comments=C1))
        for step in range(8):
            action = rng.choice(["review", "reply", "new", "approve"])
            with self.subTest(step=step, action=action):
                if action == "review":
                    self.review("design", comments=rng.choice([C1, C2, []]))
                elif action == "reply":
                    f = self.sb.tmp / ("r%d.json" % step)
                    f.write_text(json.dumps([{"reply_to": "c1", "body": "answer %d" % step}]))
                    self.gate("reply", "design", "--file", str(f), "--author", "architect")
                elif action == "new":
                    self.review("design", "--new", "notes.md", comments=C1, session="s%d" % step)
                else:
                    self.review("design", verdict="true", comments=None)
        for c in self.calls():
            with self.subTest(argv=c["argv"]):
                self.assertEqual(c["env"], ENV_WANT)
                self.assertIn("-o", c["argv"])
                self.assertEqual(c["argv"][c["argv"].index("-o") + 1], self.O)
                self.assertNotIn(c["argv"][0], FORBIDDEN)
                self.assertFalse({"--pr", "--mr"} & set(c["argv"]))
                self.assertEqual(os.path.realpath(c["cwd"]), str(self.ws))

    def test_every_round_keeps_an_export_and_numbers_only_increase_property(self):
        rng = random.Random(8)
        last = 0
        for step in range(7):
            with self.subTest(step=step):
                fresh = step == 0 or rng.random() < 0.3
                args = ("--new", "plan.md") if fresh and step else (("plan.md",) if step == 0 else ())
                verdict = rng.choice(["true", "false"])
                r = self.review("design", *args, verdict=verdict, comments=rng.choice([C1, C2, None]),
                                session="s%d" % step)
                self.ok(r)
                n = self.state()["round"]
                self.assertGreater(n, last)
                self.assertIsNotNone(self.export("design", n), "round %d has an export" % n)
                last = n
                if verdict == "true":
                    self.review("design", "plan.md", verdict="false", comments=C1, session="t%d" % step)
                    last = self.state()["round"]


if __name__ == "__main__":
    unittest.main()
