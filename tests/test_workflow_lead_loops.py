"""Checks that the rendered instructions, roles and docs make the lead ask its upfront questions
in Crit, keep watching an open PR until it merges, and treat wrapped external CLIs by what
they were seen to do.

The upfront questions go in one numbered file the human reviews in Crit, with chat as the
fallback. Once a PR is open, the lead polls it, answers feedback, pushes re-signed fixes
within limits, and stops for the human when a fix changes scope. A contract that wraps an
external CLI records real commands and output and carries one opt-in real-tool smoke test.
Patterns are case-insensitive regexes.
"""

import hashlib
import json
import re
import subprocess
import unittest

from test_workflow_rules import (DIST, INSTR, README, ROOT, WF, RulesCase, agent_texts, instr_texts, read,
                                 section)

H_QUESTIONS = "### Upfront questions in Crit"
H_WATCH = "### Watching the open PR"
H_STEPS = "### Steps by variant"
H_LEAD = "### How the lead runs the steps"
H_KICKOFF = "### Kickoff (the design step, before engineering starts)"
H_ARCH_CLI = "## Contracts that wrap an external CLI"
H_TESTER_SKIP = "### Integration tests with skip guards"
H_REVIEW_TESTS = "### 4. Review tests"

QUESTIONS_FILE = r"questionnaire\.md"
PR_LOOP = ROOT / "docs" / "pr-loop.md"
DOCS_INDEX = ROOT / "docs" / "README.md"
HARNESSES = ROOT / "docs" / "harnesses.md"
PR_LOOP_HEADINGS = ["## What the lead polls", "## When feedback is addressed", "## When the loop is done",
                    "## Fixes, scope and failing checks", "## Caps", "## Claude Code and Codex"]

# The vendored grilling skill as pinned upstream: its files and their sha256.
GRILLING = {
    "SKILL.md": "3b23fc290f79200ca4d969e3ea61b122ed1783324bf81670b28e78b1a8c84a98",
    "LICENSE": "0e7ac423bf2c6e223b7c5b156f8cf72da49d748e56a1641402c31f22ad07dbb5",
    "agents/openai.yaml": "1411d7df7d99b7e621a1ff8283c8133cc2464be63d064e52d8ce169c6800ee9b",
}
SKILL_DIRS = {"core": ROOT / "core" / "skills" / "grilling",
              "claude-code": DIST / "claude-code" / "skills" / "grilling",
              "codex": DIST / "codex" / "skills" / "grilling"}


def sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text)) if s.strip()]


def paragraphs(text):
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


class LeadLoopsCase(RulesCase):

    def instr(self, heading):
        return self.instr_section(heading)

    def role(self, role, heading):
        out = {}
        for name, doc in agent_texts(role).items():
            sec = section(doc, heading)
            self.assertIsNotNone(sec, "%s has no %r heading" % (name, heading))
            out[name] = sec
        return out

    def questions_paragraph(self):
        """The Kickoff paragraph that tells the product-owner where its questions go, per harness."""
        out = {}
        for name, sec in self.role("product-owner", H_KICKOFF).items():
            hits = [p for p in paragraphs(sec) if re.search(QUESTIONS_FILE, p, re.I)]
            self.assertTrue(hits, "%s: no Kickoff paragraph names the questions file" % name)
            out[name] = hits[0] if hits else ""
        return out


class TestUpfrontQuestionsFile(LeadLoopsCase):

    def test_product_owner_writes_upfront_questions_to_a_workspace_file_not_a_repo(self):
        for name, para in self.questions_paragraph().items():
            self.assert_has(name, para, QUESTIONS_FILE, r"workspace dir", r"never in a project repo")

    def test_each_question_is_numbered_with_a_proposed_answer_and_silence_accepts_it(self):
        for name, para in self.questions_paragraph().items():
            self.assert_has(name, para, r"numbered", r"Proposed:", r"no comment", r"accepting the proposal")

    def test_lead_opens_the_questions_in_crit_without_recording_an_approval(self):
        for name, sec in self.instr(H_QUESTIONS).items():
            self.assert_has(name, sec, QUESTIONS_FILE, r"team-guard gate review questions <file>",
                            r"writes no approval record", r"team-guard gate check`?\s+does not apply")

    def test_finished_review_answers_the_round_and_its_export_goes_to_the_product_owner(self):
        for name, sec in self.instr(H_QUESTIONS).items():
            self.assert_has(name, sec, r"\bfinish\w*", r"\banswered\b", r"zero comments",
                            r"every proposal is accepted", r"an answer or a change", r"reviews/",
                            r"product-owner")

    def test_one_follow_up_round_in_the_same_file_and_the_rest_goes_to_the_design_gate(self):
        for name, sec in self.instr(H_QUESTIONS).items():
            self.assert_has(name, sec, r"at most one follow-up", r"same file", r"design gate", r"open question")

    def test_without_crit_or_on_codex_the_questions_go_to_chat_with_grilling(self):
        for name, sec in self.instr(H_QUESTIONS).items():
            self.assert_has(name, sec, r"exits? 3", r"Codex", r"\bchat\b", r"`grilling`")

    def test_grilling_stays_vendored_unchanged_and_is_only_the_chat_fallback(self):
        for where, d in SKILL_DIRS.items():
            with self.subTest(copy=where):
                files = sorted(str(p.relative_to(d)) for p in d.rglob("*") if p.is_file())
                self.assertEqual(files, sorted(GRILLING), "%s: grilling file set changed" % where)
                for rel, digest in GRILLING.items():
                    if (d / rel).is_file():
                        self.assertEqual(hashlib.sha256((d / rel).read_bytes()).hexdigest(), digest,
                                         "%s: grilling/%s changed" % (where, rel))
        upstream = json.loads(read(ROOT / "core" / "skills" / "UPSTREAM.json"))
        self.assertEqual(upstream["skills"]["grilling"]["status"], "as-is")

        for name, doc in agent_texts("product-owner").items():
            for s in sentences(doc):
                if re.search(r"grilling", s, re.I):
                    with self.subTest(file=name, sentence=s[:120]):
                        self.assertRegex(s, re.compile(r"Crit is unavailable.*grilling", re.I),
                                         "%s names grilling outside the Crit-unavailable fallback" % name)
        for name, sec in self.instr(H_STEPS).items():
            self.assert_has(name, sec, r"one upfront round of questions", r"Upfront questions in Crit",
                            r"`grilling`")
            self.assert_lacks(name, sec, r"product-owner runs the upfront round with the `grilling` skill")
        for name, doc in instr_texts().items():
            self.assert_has(name, doc, r"`grilling` \(chat fallback")
        for name, path in (("docs/workflow", WF), ("docs/harnesses", HARNESSES), ("README", README)):
            for line in read(path).splitlines():
                if re.search(r"grilling", line, re.I):
                    with self.subTest(file=name, line=line.strip()[:120]):
                        self.assertRegex(line, re.compile(r"\bfallback\b", re.I),
                                         "%s describes grilling as more than the chat fallback" % name)


class TestWatchingTheOpenPR(LeadLoopsCase):

    def test_lead_keeps_watching_an_open_pr_until_it_is_merged_or_closed(self):
        for name, sec in self.instr(H_WATCH).items():
            self.assert_has(name, sec, r"until it is merged or closed")

    def test_lead_polls_checks_threads_and_comments_and_any_new_item_is_feedback(self):
        for name, sec in self.instr(H_WATCH).items():
            self.assert_has(name, sec, r"every 2 minutes", r"status checks", r"review threads", r"unresolved",
                            r"conversation comments", r"review summaries", r"changes requested", r"\bnew\b",
                            r"feedback", r"pr-loop\.md")
        self.assertTrue(PR_LOOP.is_file(), "docs/pr-loop.md exists")
        doc = read(PR_LOOP)
        self.assertEqual(doc.splitlines()[0].rstrip(), "# Watch an open PR")
        for h in PR_LOOP_HEADINGS:
            with self.subTest(heading=h):
                self.assertIsNotNone(section(doc, h), "docs/pr-loop.md has no %r heading" % h)
        self.assert_has("docs/pr-loop", section(doc, "## What the lead polls"), r"gh pr checks", r"reviewThreads",
                        r"gh pr view\b[^\n]*--comments", r"gh api\b[^\n]*pulls/[^\n]*/reviews")
        index = [l for l in read(DOCS_INDEX).splitlines() if l.lstrip().startswith("|")]
        self.assertTrue(any(re.search(r"\]\(\./pr-loop\.md\)|\]\(pr-loop\.md\)", l) for l in index),
                        "docs/README.md's guide table lists the PR loop guide")

    def test_a_comment_is_addressed_by_a_reply_plus_a_fix_or_a_reason_and_threads_stay_open(self):
        for name, sec in self.instr(H_WATCH).items():
            self.assert_has(name, sec, r"addressed", r"replied", r"pushed a fix", r"explained why not",
                            r"never resolves threads")

    def test_loop_is_done_when_checks_are_green_threads_answered_and_a_poll_is_empty(self):
        for name, sec in self.instr(H_WATCH).items():
            self.assert_has(name, sec, r"\bdone\b", r"\bgreen\b", r"every thread has a lead reply", r"one poll")

    def test_lead_pushes_review_fixes_re_signed_without_asking_again(self):
        for name, sec in self.instr(H_WATCH).items():
            self.assert_has(name, sec, r"team-guard sign", r"without asking again")

    def test_a_fix_that_changes_scope_stops_the_lead_to_ask_before_pushing(self):
        for name, sec in self.instr(H_WATCH).items():
            self.assert_has(name, sec, r"changes? scope", r"new behavior", r"contract", r"sealed test",
                            r"stop and ask", r"before push\w*")

    def test_a_check_that_keeps_failing_or_fails_outside_the_change_is_reported_not_fixed(self):
        for name, sec in self.instr(H_WATCH).items():
            self.assert_has(name, sec, r"two fix attempts", r"flaky", r"infra", r"secret", r"check name",
                            r"log excerpt", r"what it tried", r"keeps watching")

    def test_waiting_does_not_count_toward_turn_caps_and_the_loop_has_time_and_push_caps(self):
        for name, sec in self.instr(H_WATCH).items():
            self.assert_has(name, sec, r"waiting turns", r"turn caps", r"4 hours", r"10 fix pushes",
                            r"\breports?\b", r"\bwaits?\b")

    def test_claude_code_polls_in_the_background_and_codex_checks_once_per_turn(self):
        for name, sec in self.instr(H_WATCH).items():
            self.assert_has(name, sec, r"Claude Code", r"background", r"re-arm", r"Codex", r"one check per turn",
                            r"re-prompt")

    def test_the_first_push_of_a_pr_still_needs_the_humans_approval(self):
        for name, sec in self.instr(H_LEAD).items():
            self.assert_has(name, sec, r"never pushes without the human's approval")
        for name, sec in self.instr(H_WATCH).items():
            self.assert_has(name, sec, r"first push[^.\n]{0,120}approv|approv\w*[^.\n]{0,120}first push")


class TestExternalCliContracts(LeadLoopsCase):

    def test_architect_records_the_commands_run_and_their_real_output(self):
        for name, sec in self.role("architect", H_ARCH_CLI).items():
            self.assert_has(name, sec, r"commands actually run", r"real output", r"not assumed behavior")

    def test_architect_and_tester_require_one_opt_in_real_tool_smoke_test_outside_the_seal(self):
        for name, sec in self.role("architect", H_ARCH_CLI).items():
            self.assert_has(name, sec, r"one smoke test", r"real tool", r"TEAM_REAL_TOOLS=1", r"skipped",
                            r"not sealed", r"unit tests may use fakes")
        for name, sec in self.role("tester", H_TESTER_SKIP).items():
            self.assert_has(name, sec, r"smoke test", r"TEAM_REAL_TOOLS=1", r"skip", r"(never|not) sealed",
                            r"fakes?")

    def test_reviewer_flags_a_cli_contract_without_observed_behavior_or_a_smoke_test(self):
        for name, sec in self.role("reviewer", H_REVIEW_TESTS).items():
            self.assert_has(name, sec, r"\bCLI\b", r"observed behavior", r"smoke test", r"\bmajor\b")


class TestInstructionsSize(LeadLoopsCase):

    def test_rendered_instruction_files_stay_within_32_kib_and_match_the_build(self):
        for name, path in INSTR.items():
            with self.subTest(file=name):
                self.assertLessEqual(path.stat().st_size, 32768)
                p = subprocess.run([str(ROOT / "build.py"), "--check", name], cwd=str(ROOT), capture_output=True,
                                   text=True, timeout=120)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertIn("up to date", p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
