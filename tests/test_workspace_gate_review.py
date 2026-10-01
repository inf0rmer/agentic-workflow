"""team-guard gate review: run a local Crit review round for a design gate and keep its data.

Approval deletes Crit's review data, so every round is exported as it runs. Each review runs
from its own directory under .crit, because Crit keys a session on its working directory
and files; re-running from the same directory continues or resumes the same session. The
review is started detached, so a killed wait loses nothing.
"""

import json
import os
import random
import signal
import unittest

from wsgate import (APPROVED_OUT, FORBIDDEN, NOT_APPROVED_OUT, SIDECAR_NOTE, GateCase, LineReader,
                    approval_sidecar_name, export_name, sha, verdict_line, wait_until)
from wstools import pid_alive

C1 = [{"id": "c1", "body": "why this?", "replies": [{"body": "because"}]}, {"id": "c2", "body": "ok", "resolved": True}]
C2 = C1 + [{"id": "c3", "body": "and this?"}]
ENV_WANT = {"CRIT_NO_UPDATE_CHECK": "1", "CRIT_PLAN_REVIEW": "off", "CRIT_SHARE_URL": ""}


def starts(calls):
    return [c for c in calls if c["argv"][:1] not in (["comments"], ["comment"])]


def live_export(session):
    return ["comments", "--session", session, "--json", "--all"]


class TestReviewStart(GateCase):

    def test_new_review_runs_crit_locally_and_streams_its_output(self):
        hold = self.sb.tmp / "hold"
        self.set_comments(C1)
        p = self.popen_gate("review", "design", "plan.md", "notes.md", env={"FAKE_CRIT_HOLD": str(hold)})
        err = LineReader(p.stderr)
        try:
            seen = err.wait_for("Started crit daemon at http://localhost:47111 (session ")
        finally:
            hold.write_text("go")
        out = p.stdout.read()
        p.wait(timeout=30)
        err.wait_for("\x00never", timeout=5)
        self.assertTrue(seen, "the session line reached the caller while Crit was still running")
        self.assertEqual(p.returncode, 0, "\n".join(err.lines))
        self.assertIn("approved: false", err.lines)
        start = starts(self.calls())
        self.assertEqual(len(start), 1)
        self.assertEqual(start[0]["argv"], ["-o", self.O] + self.abs_files("plan.md", "notes.md"))
        self.assertEqual(os.path.realpath(start[0]["cwd"]), self.review_dir("design", 1))
        for c in self.calls():
            with self.subTest(argv=c["argv"]):
                self.assertEqual(c["env"], ENV_WANT)
                self.assertEqual(os.path.realpath(c["cwd"]), self.review_dir("design", 1))
        self.assertTrue(out.endswith("\n"))

        self.ok(self.review("other", "plan.md", "--no-open"))
        last = starts(self.calls())[-1]
        self.assertEqual(last["argv"], ["-o", self.O, "--no-open"] + self.abs_files("plan.md"))
        self.assertEqual(os.path.realpath(last["cwd"]), self.review_dir("other", 1), "each gate has its own review")

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
        self.code(self.gate("review", "design", "plan.md", cwd=self.sb.scratch), 1)
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
            for gate, literal in (("design", "approve design"), ("D1", "approve D1"), ("D2", "approve D2")):
                with self.subTest(bin=binpath.name, gate=gate):
                    r = self.code(self.gate("review", gate, "plan.md", env={"CRIT_BIN": str(binpath)}), 3)
                    self.assertIn('team-guard: gate %s: Crit is unavailable; use the chat fallback: the reply that '
                                  'approves this gate is exactly "%s"' % (gate, literal), r.err.splitlines())
                    self.assertEqual(self.ws_snapshot(), before)
            # Only the design gates have an approving chat reply; any other gate is told to post in
            # chat and is given no reply to wait for.
            with self.subTest(bin=binpath.name, gate="alpha"):
                r = self.code(self.gate("review", "alpha", "plan.md", env={"CRIT_BIN": str(binpath)}), 3)
                hint = [l for l in r.err.splitlines() if l.startswith("team-guard: gate alpha: ")]
                self.assertEqual(len(hint), 1, self.show(r))
                advice = hint[0][len("team-guard: gate alpha: "):]
                self.assertRegex(advice, r"^Crit is unavailable")
                self.assertRegex(advice, r"(?i)\bpost\b[^\n]*\bin chat\b")
                self.assertNotRegex(r.err, r"(?i)approves this gate")
                self.assertNotRegex(r.err, r'"approve [^"]*"')
                self.assertEqual(self.ws_snapshot(), before)


class TestReviewRounds(GateCase):

    def test_unfinished_review_continues_its_session_as_a_new_round(self):
        self.ok(self.review("design", "plan.md", "notes.md", comments=C1))
        self.assertEqual(self.export("design", 1), C1)
        sid = self.session_of("design", 1, "plan.md", "notes.md")
        n_before = len(self.calls())
        self.set_comments(C2)
        r = self.ok(self.review("design", comments=None))
        new = self.calls()[n_before:]
        self.assertEqual(new[0]["argv"], live_export(sid), "the outgoing round is exported first")
        cont = starts(new)
        self.assertEqual([c["argv"] for c in cont], [["-o", self.O] + self.abs_files("plan.md", "notes.md")])
        self.assertEqual(os.path.realpath(cont[0]["cwd"]), self.review_dir("design", 1), "the same review dir")
        self.assertEqual(r.out.splitlines()[-1], verdict_line("design", 2, False, 2))
        self.assertEqual(self.export("design", 2), C2)
        self.assertEqual(self.export("design", 1), C2, "the outgoing round was re-exported with the replies")

        r = self.code(self.review("design", "plan.md", comments=None), 1)
        self.has(r.err, "--new", r)
        self.ok(self.review("design", "notes.md", "plan.md", comments=None))
        self.assertEqual(self.state()["round"], 3)
        self.assertEqual(starts(self.calls())[-1]["argv"], ["-o", self.O] + self.abs_files("plan.md", "notes.md"),
                         "files keep their stored order")

        kept = {n: (self.ws / "reviews" / export_name("design", n)).read_bytes() for n in (1, 2, 3)}
        n_before = len(self.calls())
        self.ok(self.review("design", "--new", "plan.md", comments=C1))
        new = self.calls()[n_before:]
        self.assertEqual(new[0]["argv"], live_export(sid), "the outgoing round is exported before a fresh review")
        st = self.state()
        self.assertEqual((st["round"], st["open_round"], st["files"]), (4, 4, ["plan.md"]))
        fresh = starts(new)[0]
        self.assertEqual(fresh["argv"], ["-o", self.O] + self.abs_files("plan.md"))
        self.assertEqual(os.path.realpath(fresh["cwd"]), self.review_dir("design", 4))
        self.assertNotEqual(st["session"], sid, "a fresh review gets its own session")
        for n in (1, 2):
            self.assertEqual((self.ws / "reviews" / export_name("design", n)).read_bytes(), kept[n])

        self.ok(self.review("design", comments=None, verdict="true"))
        n = self.state()["round"]
        self.ok(self.review("design", "notes.md", comments=[]))
        st = self.state()
        self.assertEqual((st["round"], st["open_round"]), (n + 1, n + 1), "a reopen opens a fresh review")
        self.assertEqual(os.path.realpath(starts(self.calls())[-1]["cwd"]), self.review_dir("design", n + 1))
        for m in range(1, n + 1):
            self.assertTrue((self.ws / "reviews" / export_name("design", m)).exists(), m)

    def test_verdict_is_reported_on_its_own_line_stored_and_approval_hashes_the_files(self):
        r = self.ok(self.review("design", "plan.md", "notes.md", comments=C2))
        lines = r.out.splitlines()
        self.assertEqual(lines[-2:], [NOT_APPROVED_OUT % 2, verdict_line("design", 1, False, 2)],
                         "Crit's unterminated last line is kept apart from the verdict line")
        st = self.state()
        self.assertEqual((st["approved"], st["session"], st["round"]),
                         (False, self.session_of("design", 1, "plan.md", "notes.md"), 1))
        self.assertIsNone(st.get("pending"))
        r = self.ok(self.review("design", comments=C1, verdict="true"))
        self.assertEqual(r.out.splitlines()[-2:], [APPROVED_OUT, verdict_line("design", 2, True, 0)],
                         "approved: true is found although it isn't Crit's last stderr line, and an approved "
                         "round reports no open comments")
        self.assertEqual(self.export("design", 2), C1, "the round's export is left as it was")
        sidecar = json.loads((self.ws / "reviews" / approval_sidecar_name("design", 2)).read_text())
        self.assertRegex(sidecar.pop("at"), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
        self.assertEqual(sidecar, {"version": 1, "gate": "design", "round": 2, "approved": True, "note": SIDECAR_NOTE},
                         "the empty final export is explained next to the kept one")
        self.assertFalse((self.ws / "reviews" / approval_sidecar_name("design", 1)).exists(),
                         "no sidecar for a round that wasn't approved")
        st = self.state()
        self.assertTrue(st["approved"])
        self.assertEqual(st["hashes"], {"plan.md": sha(self.ws / "plan.md"), "notes.md": sha(self.ws / "notes.md")})

        for label, env in (("non-zero exit", {"FAKE_CRIT_EXIT": "2"}), ("no verdict", {"FAKE_CRIT_VERDICT": "none"}),
                           ("daemon shut down", {"FAKE_CRIT_VERDICT": "shutdown"})):
            gate = "g" + label.replace(" ", "-")
            with self.subTest(case=label):
                self.ok(self.review(gate, "plan.md", comments=C1))
                before = self.state(gate)
                n_calls = len(self.calls())
                self.set_comments(C2)
                e = {"FAKE_CRIT_VERDICT": "false"}
                e.update(env)
                self.code(self.gate("review", gate, env=e), 1)
                after = self.state(gate)
                for k in ("round", "approved", "session", "hashes", "open_round"):
                    self.assertEqual(after.get(k), before.get(k), k)
                self.assertIsNotNone(after.get("pending"), "the round can be resumed")
                self.assertIn("comments", [c["argv"][0] for c in self.calls()[n_calls:]][1:],
                              "the round is still exported after Crit fails")
                self.assertEqual(self.export(gate, 2), C2)

    def test_round_is_exported_periodically_while_crit_runs(self):
        hold = self.sb.tmp / "hold"
        self.addCleanup(lambda: hold.exists() or hold.write_text("go"))
        self.set_comments(C1)
        p = self.popen_gate("review", "design", "plan.md", env={"FAKE_CRIT_HOLD": str(hold),
                                                                 "TEAM_GATE_EXPORT_INTERVAL_SEC": "1"})
        self.assertTrue(LineReader(p.stderr).wait_for("(session "))
        self.assertTrue(wait_until(lambda: self.export("design", 1) == C1))
        self.set_comments(C2)
        self.assertTrue(wait_until(lambda: self.export("design", 1) == C2, timeout=15),
                        "the export follows new comments before Crit exits")
        self.assertIsNone(p.poll(), "Crit is still running")
        hold.write_text("go")
        p.communicate(timeout=30)
        self.assertEqual(p.returncode, 0)

    def test_failed_export_write_still_reports_and_stores_the_verdict(self):
        hold = self.sb.tmp / "hold"
        self.addCleanup(lambda: hold.exists() or hold.write_text("go"))
        self.set_comments(C1)
        p = self.popen_gate("review", "design", "plan.md", env={"FAKE_CRIT_HOLD": str(hold)})
        # One reader per pipe: this thread owns stderr for the whole run.
        err_reader = LineReader(p.stderr)
        self.assertTrue(err_reader.wait_for("(session "))
        self.assertTrue(wait_until(lambda: self.export("design", 1) == C1))
        reviews = self.ws / "reviews"
        self.set_comments(C2)
        os.chmod(reviews, 0o555)
        try:
            hold.write_text("go")
            out = p.stdout.read()
            p.wait(timeout=30)
            err_reader.wait_for("\x00never", timeout=10)  # returns at end of stream
        finally:
            os.chmod(reviews, 0o755)
        err = "\n".join(err_reader.lines)
        self.assertEqual(p.returncode, 0, err)
        self.assertTrue(out.splitlines()[-1].startswith(verdict_line("design", 1, False, 0)[:-1]), out)
        self.assertTrue(any(l.startswith("team-guard: warning: could not write reviews/%s: " % export_name("design", 1))
                            for l in err.splitlines()), err)
        st = self.state()
        self.assertEqual((st["approved"], st["round"]), (False, 1))
        self.assertIsNone(st.get("pending"))


class TestKilledWait(GateCase):

    def test_review_is_detached_exported_at_start_and_resumed_after_a_kill(self):
        hold = self.sb.tmp / "hold"
        self.set_comments(C1)
        sid = self.session_of("design", 1, "plan.md")
        p = self.popen_gate("review", "design", "plan.md", env={"FAKE_CRIT_HOLD": str(hold)})
        self.addCleanup(lambda: hold.exists() or hold.write_text("go"))
        err = LineReader(p.stderr)
        self.assertTrue(err.wait_for("(session %s" % sid), "the review started")
        self.assertTrue(wait_until(lambda: self.export("design", 1) == C1), "the first round is exported while Crit runs")
        self.assertEqual(self.state()["pending"], {"round": 1, "session": sid})

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
        self.assertEqual([c["argv"] for c in cont], [["-o", self.O] + self.abs_files("plan.md")])
        self.assertEqual(os.path.realpath(cont[0]["cwd"]), self.review_dir("design", 1))
        self.assertIn("Connected to crit daemon at http://localhost:47111 (session %s)" % sid, r.err.splitlines())
        self.assertEqual(r.out.splitlines()[-1], verdict_line("design", 1, False, 2))
        st = self.state()
        self.assertIsNone(st.get("pending"))
        self.assertEqual((st["round"], st["session"]), (1, sid))

    def test_new_after_a_killed_wait_discards_the_pending_round(self):
        hold = self.sb.tmp / "hold"
        self.set_comments(C1)
        p = self.popen_gate("review", "design", "plan.md", env={"FAKE_CRIT_HOLD": str(hold)})
        self.addCleanup(lambda: hold.exists() or hold.write_text("go"))
        self.assertTrue(LineReader(p.stderr).wait_for("(session "))
        self.assertTrue(wait_until(lambda: (self.state() or {}).get("pending") is not None))
        first_cwd = os.path.realpath(starts(self.calls())[0]["cwd"])
        os.killpg(p.pid, signal.SIGKILL)
        p.wait(timeout=30)
        hold.write_text("go")
        n_before = len(self.calls())
        self.ok(self.review("design", "--new", "plan.md", comments=C1))
        fresh = starts(self.calls()[n_before:])[0]
        self.assertEqual(fresh["argv"], ["-o", self.O] + self.abs_files("plan.md"))
        st = self.state()
        self.assertIsNone(st.get("pending"))
        self.assertNotEqual(os.path.realpath(fresh["cwd"]), first_cwd)
        self.assertEqual(os.path.realpath(fresh["cwd"]), self.review_dir("design", st["open_round"]))


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
        self.assertTrue(all(os.path.realpath(c["cwd"]).startswith(self.O + os.sep) for c in self.calls()))


class TestReviewInvariants(GateCase):

    def test_crit_is_only_ever_run_locally_property(self):
        rng = random.Random(4)
        self.ok(self.review("design", "plan.md", comments=C1))
        for step in range(8):
            action = rng.choice(["review", "reply", "new", "approve", "crash"])
            with self.subTest(step=step, action=action):
                if action == "review":
                    self.review("design", comments=rng.choice([C1, C2, []]))
                elif action == "reply":
                    f = self.sb.tmp / ("r%d.json" % step)
                    f.write_text(json.dumps([{"reply_to": "c1", "body": "answer %d" % step}]))
                    self.gate("reply", "design", "--file", str(f), "--author", "architect")
                elif action == "new":
                    self.review("design", "--new", "notes.md", comments=C1)
                elif action == "crash":
                    st = self.state() or {}
                    if st.get("session"):
                        self.end_daemon(st["session"])
                    self.review("design", comments=C2)
                else:
                    self.review("design", verdict="true", comments=None)
        for c in self.calls():
            a = c["argv"]
            with self.subTest(argv=a):
                self.assertEqual(c["env"], ENV_WANT)
                self.assertNotIn(a[0], FORBIDDEN)
                self.assertFalse({"--pr", "--mr"} & set(a))
                self.assertFalse("-o" in a and "--session" in a, "real Crit rejects -o with --session")
                self.assertRegex(os.path.realpath(c["cwd"]), "^%s/design/r[0-9]+$" % self.O)
                if a[0] == "comments":
                    self.assertTrue(a[:2] == ["comments", "--session"] and a[3:] == ["--json", "--all"] or
                                    a[:5] == ["comments", "-o", self.O, "--json", "--all"] and
                                    a[5].startswith(self.O + "/reviews/") and a[5].endswith("/review.json"), a)
                elif a[0] == "comment":
                    self.assertEqual((a[:2], a[3:5]), (["comment", "--session"], ["--json", "--author"]))
                else:
                    self.assertEqual(a[:2], ["-o", self.O])

    def test_every_round_keeps_an_export_and_numbers_only_increase_property(self):
        rng = random.Random(8)
        last = 0
        for step in range(7):
            with self.subTest(step=step):
                fresh = step == 0 or rng.random() < 0.3
                args = ("--new", "plan.md") if fresh and step else (("plan.md",) if step == 0 else ())
                verdict = rng.choice(["true", "false"])
                r = self.review("design", *args, verdict=verdict, comments=rng.choice([C1, C2, None]))
                self.ok(r)
                n = self.state()["round"]
                self.assertGreater(n, last)
                self.assertIsNotNone(self.export("design", n), "round %d has an export" % n)
                last = n
                if verdict == "true":
                    self.review("design", "plan.md", verdict="false", comments=C1)
                    last = self.state()["round"]


if __name__ == "__main__":
    unittest.main()
