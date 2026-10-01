"""team-guard gate record and gate check: the approval record and whether it still holds.

The record names exactly the bytes the human approved, so a later change to a reviewed file
is caught before more tests are sealed on top of it. The record file is only ever appended
to, and anything hand-written in it is left alone.
"""

import hashlib
import random
import unittest

from wsgate import GateCase, GATE_RECORD, sha, today


def section(heading, rows):
    fence = "`" * 3
    return "\n## %s\n\n%s\n%s%s\n" % (heading, fence, "".join("%s  %s\n" % (h, p) for h, p in rows), fence)


class TestRecordCrit(GateCase):

    def approve(self, gate, files, rounds=1):
        self.ok(self.review(gate, *files, verdict="true" if rounds == 1 else "false", comments=[], session=gate))
        for i in range(rounds - 1):
            self.ok(self.review(gate, verdict="true" if i == rounds - 2 else "false", comments=[], session=gate))

    def test_record_appends_the_approved_hashes_in_the_record_format(self):
        self.approve("design", ["plan.md", "notes.md"])
        r = self.ok(self.gate("record", "design", "--crit"))
        self.assertEqual(r.out, "gate design: recorded 2 file(s) in %s\n" % GATE_RECORD)
        want = "# Design gate record\n" + section(
            "design: approved %s in Crit (1 round, approved: true)" % today(),
            [(sha(self.ws / "plan.md"), "plan.md"), (sha(self.ws / "notes.md"), "notes.md")])
        self.assertEqual(self.record_path().read_text(), want)
        self.assertEqual(sha(self.ws / "plan.md"), hashlib.sha256(b"# plan\n").hexdigest())

        self.approve("D2", ["notes.md"], rounds=3)
        before = self.record_path().read_bytes()
        self.ok(self.gate("record", "D2", "--crit"))
        added = self.record_path().read_bytes()[len(before):].decode()
        self.assertEqual(added, section("D2: approved %s in Crit (3 rounds, approved: true)" % today(),
                                        [(sha(self.ws / "notes.md"), "notes.md")]))

    def test_record_refuses_an_unapproved_or_changed_review(self):
        self.code(self.gate("record", "design", "--crit"), 1)
        self.ok(self.review("design", "plan.md", "notes.md", comments=[]))
        r = self.code(self.gate("record", "design", "--crit"), 1)
        self.has(r.err, "design", r)
        self.assertFalse(self.record_path().exists())
        self.ok(self.review("design", verdict="true", comments=[]))
        (self.ws / "plan.md").write_text("# plan, edited\n")
        (self.ws / "notes.md").unlink()
        r = self.code(self.gate("record", "design", "--crit"), 1)
        self.has(r.err, "plan.md", r)
        self.has(r.err, "notes.md", r)
        self.assertFalse(self.record_path().exists(), "nothing is appended")
        self.nonzero(self.gate("record", "design", "--crit", "--chat", "approve design"))
        self.nonzero(self.gate("record", "design"))

        (self.ws / "plan.md").write_text("# plan\n")
        (self.ws / "notes.md").write_text("# notes\n")
        self.ok(self.gate("record", "design", "--crit"))
        before = self.record_path().read_bytes()
        r = self.ok(self.gate("record", "design", "--crit"))
        self.assertEqual(r.out, "gate design: already recorded\n")
        self.assertEqual(self.record_path().read_bytes(), before)
        self.code(self.gate("record", "design", "plan.md", "--crit"), 1)


class TestRecordChat(GateCase):

    def test_chat_approval_needs_the_exact_reply_and_no_crit(self):
        env = {"CRIT_BIN": str(self.sb.tmp / "no-crit-here")}
        r = self.ok(self.gate("record", "D1", "plan.md", "--chat", "  approve D1  ", env=env))
        self.assertEqual(self.record_path().read_text(), "# Design gate record\n" + section(
            "D1: approved %s in chat (reply: approve D1)" % today(), [(sha(self.ws / "plan.md"), "plan.md")]))
        before = self.record_path().read_bytes()
        for gate, literal, replies in (("D1", "approve D1", ["approved", "Approve D1", "approve D2", "lgtm", "", "approve design"]),
                                       ("design", "approve design", ["Approve design", "approve  design", "approve D1"])):
            for reply in replies:
                with self.subTest(gate=gate, reply=reply):
                    r = self.code(self.gate("record", gate, "plan.md", "--chat", reply, env=env), 1)
                    self.assertIn('team-guard: gate %s: the reply that approves this gate is exactly "%s"'
                                  % (gate, literal), r.err.splitlines())
                    self.assertEqual(self.record_path().read_bytes(), before)
        # Only the design gates have an approving chat reply: no reply records any other gate.
        for gate in ("alpha", "questions"):
            for reply in ("approve design", "approve %s" % gate):
                with self.subTest(gate=gate, reply=reply):
                    r = self.code(self.gate("record", gate, "plan.md", "--chat", reply, env=env), 1)
                    self.no_traceback(r)
                    self.assertIn("this gate has no approval record", r.err)
                    self.assertNotIn("approves this gate is exactly", r.err)
                    self.assertEqual(self.record_path().read_bytes(), before)
        self.code(self.gate("record", "design", "--chat", "approve design", env=env), 1)
        self.code(self.gate("record", "design", "missing.md", "--chat", "approve design", env=env), 1)
        outside = self.sb.tmp / "outside.md"
        outside.write_text("x\n")
        self.code(self.gate("record", "design", str(outside), "--chat", "approve design", env=env), 1)
        self.assertEqual(self.record_path().read_bytes(), before)
        self.ok(self.gate("record", "D2", "notes.md", "plan.md", "--chat", "approve D2", env=env))
        self.assertTrue(self.record_path().read_text().endswith(section(
            "D2: approved %s in chat (reply: approve D2)" % today(),
            [(sha(self.ws / "notes.md"), "notes.md"), (sha(self.ws / "plan.md"), "plan.md")])))
        self.assertEqual(self.calls(), [], "chat approval never runs Crit")

    def test_record_only_appends_and_leaves_hand_written_text_alone_property(self):
        start = "# Design gate record\n\nNotes the lead wrote by hand.\n\n## D2: approved 2026-01-01 by hand\n\nno fence"
        self.record_path().write_text(start)
        rng = random.Random(17)
        prev = self.record_path().read_bytes()
        self.ok(self.gate("record", "design", "plan.md", "--chat", "approve design"))
        now = self.record_path().read_bytes()
        self.assertTrue(now.startswith(prev + b"\n\n## design: approved"), now[len(prev):len(prev) + 30])
        for i in range(6):
            with self.subTest(iteration=i):
                prev = now
                target = rng.choice(["plan.md", "notes.md"])
                (self.ws / target).write_text("# %s %d\n" % (target, rng.randint(0, 99)))
                gate = rng.choice(["design", "D1", "D2"])
                self.ok(self.gate("record", gate, target, "--chat", "approve " + gate))
                now = self.record_path().read_bytes()
                self.assertTrue(now.startswith(prev))


class TestCheck(GateCase):

    def test_check_reports_whether_the_recorded_approval_still_matches(self):
        self.ok(self.gate("record", "design", "plan.md", "notes.md", "--chat", "approve design"))
        before = self.ws_snapshot()
        r = self.ok(self.gate("check", "design"))
        self.assertEqual(r.out, "gate design: matches the approval of %s\n" % today())
        r = self.ok(self.gate("check"))
        self.assertEqual(r.out, "gate design: matches the approval of %s\n" % today())
        (self.ws / "plan.md").write_text("changed\n")
        (self.ws / "notes.md").unlink()
        r = self.code(self.gate("check", "design"), 2)
        self.assertEqual(r.err, "gate design: reopen the gate\nchanged: plan.md\nmissing: notes.md\n")
        r = self.code(self.gate("check", "D1"), 2)
        self.assertEqual(r.err, "gate D1: reopen the gate\nno approval recorded\n")
        self.code(self.gate("check", "../x"), 1)
        self.code(self.gate("check", "design", cwd=self.sb.scratch), 1)
        after = self.ws_snapshot()
        self.assertEqual(set(after), set(before) - {"notes.md"}, "check writes nothing")
        self.assertEqual(self.calls(), [])

        self.record_path().unlink()
        self.code(self.gate("check", "design"), 1)
        r = self.code(self.gate("check"), 2)
        self.assertEqual(r.err, "gate -: reopen the gate\nno approval recorded\n")

    def test_record_then_check_round_trips_and_parses_hand_written_records_property(self):
        (self.ws / "c.md").write_bytes(b"content\n")
        self.ok(self.gate("record", "design", "plan.md", "notes.md", "c.md", "--chat", "approve design"))
        rng = random.Random(29)
        for i in range(6):
            with self.subTest(iteration=i):
                self.ok(self.gate("check", "design"))
                target = self.ws / rng.choice(["plan.md", "notes.md", "c.md"])
                data = bytearray(target.read_bytes())
                at = rng.randrange(len(data))
                orig = bytes(data)
                data[at] = (data[at] + 1) % 256
                target.write_bytes(bytes(data))
                self.code(self.gate("check", "design"), 2)
                target.write_bytes(orig)
                self.ok(self.gate("check", "design"))

        fence = "`" * 3
        good = sha(self.ws / "plan.md")
        stale = "0" * 64
        hand = ("# Design gate record\n\nWritten by hand before the tool existed.\n\n"
                "## D2 (first pass): approved 2026-09-01 after two rounds in Crit\n\n%s\n%s  plan.md\n%s\n\n"
                "## D2: approved 2026-09-02 after a revision\n\n%s\n%s  notes.md\n%s  plan.md\n%s\n\n"
                "Note: notes.md was edited after approval. New sha256 %s.\n"
                % (fence, stale, fence, fence, sha(self.ws / "notes.md"), good, fence, "f" * 64))
        self.record_path().write_text(hand)
        r = self.ok(self.gate("check", "D2"))
        self.assertEqual(r.out, "gate D2: matches the approval of 2026-09-02\n")
        r = self.ok(self.gate("check"))
        self.assertEqual(r.out, "gate D2: matches the approval of 2026-09-02\n")
        self.assertEqual(self.record_path().read_text(), hand)


if __name__ == "__main__":
    unittest.main()
