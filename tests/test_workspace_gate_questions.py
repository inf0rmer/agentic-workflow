"""team-guard gate review for the upfront questions: a review round that approves nothing.

The questions file is reviewed with the same gate command as a design gate, under the gate
name `questions`. The round is exported like any other, but no approval record is written.
When Crit is unavailable, the hint for this gate sends the questions to chat and names no
approving reply, since no reply approves a questions round.
"""

import unittest

from wsgate import GateCase, export_name

COMMENTS = [{"id": "c1", "body": "Q2: make it 3 hours instead"}]


class TestQuestionsReview(GateCase):

    def setUp(self):
        super().setUp()
        (self.ws / "questionnaire.md").write_text("1. Which harness?\n   Proposed: both.\n")

    def test_questions_review_exports_the_round_and_records_no_approval(self):
        self.ok(self.review("questions", "questionnaire.md", comments=COMMENTS))
        self.assertEqual(self.export("questions", 1), COMMENTS)
        self.assertTrue((self.ws / "reviews" / export_name("questions", 1)).is_file())
        self.assertFalse(self.record_path().exists(), "a questions review writes no approval record")

    def test_unavailable_crit_sends_questions_to_chat_without_naming_an_approving_reply(self):
        before = self.ws_snapshot()
        r = self.code(self.gate("review", "questions", "questionnaire.md",
                                env={"CRIT_BIN": str(self.sb.tmp / "missing-crit")}), 3)
        self.no_traceback(r)
        hint = [l for l in r.err.splitlines() if l.startswith("team-guard: gate questions:")]
        self.assertEqual(len(hint), 1, self.show(r))
        advice = hint[0][len("team-guard: gate questions:"):]
        self.assertRegex(advice, r"Crit is unavailable")
        self.assertRegex(advice, r"(?i)\bchat\b")
        self.assertRegex(advice, r"(?i)question")
        self.assertNotRegex(r.err, r"(?i)approves this gate")
        self.assertNotRegex(r.err, r'"approve [^"]*"')
        self.assertEqual(self.ws_snapshot(), before)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
