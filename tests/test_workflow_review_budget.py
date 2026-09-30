"""Checks that the rendered instructions, agent files and docs keep PRs reviewable.

Each slice of a Full change is its own PR, every PR stays within a review budget, the
reviewer leaves a guide to where a human should look, and the lead follows plain stop
rules rather than a goal loop. Patterns are case-insensitive regexes.
"""

import json
import re
import unittest

from test_workflow_rules import (ROOT, INSTR, WF, README, RulesCase, agent_texts, instr_texts, read,
                                 section)
from wstools import ToolCase

H_PRS = "### Commit messages and PRs"
H_LEAD = "### How the lead runs the steps"
H_FOOTER = "### Merge-risk footer"
GUARD_RAILS = ROOT / "docs" / "guard-rails.md"
GUIDE = r"review-guide\.md"
GUIDE_HEADINGS = [r"Where to look first", r"Checked mechanically", r"Not verified", r"Provisional decisions"]
APPROVAL_STEP = "team-guard routing record step --step over-budget-approved"


def instr_sections(case, heading):
    out = {}
    for name, doc in instr_texts().items():
        sec = section(doc, heading)
        case.assertIsNotNone(sec, "%s has no %r heading" % (name, heading))
        out[name] = sec
    return out


def agent_sections(case, role, heading):
    out = {}
    for name, doc in agent_texts(role).items():
        sec = section(doc, heading)
        case.assertIsNotNone(sec, "%s has no %r heading" % (name, heading))
        out[name] = sec
    return out


def template_section(doc, heading):
    """A section of a template shown inside a code block: from `heading` to the next heading or fence."""
    lines = doc.splitlines()
    for i, line in enumerate(lines):
        if line.rstrip() == heading:
            out = [line]
            for nxt in lines[i + 1:]:
                if re.match(r"^#{1,2}\s", nxt) or nxt.lstrip().startswith("```"):
                    break
                out.append(nxt)
            return "\n".join(out)
    return None


class TestPullRequestRules(RulesCase):

    def test_full_opens_one_pr_per_slice_and_other_variants_one_pr_within_budget(self):
        for name, sec in instr_sections(self, H_PRS).items():
            self.assert_has(name, sec, r"each slice gets its own PR", r"stacked on", r"team-guard sign",
                            r"per slice", r"Standard, Bug fix and Chore", r"one PR", r"Spike", r"no PR",
                            r"\b400\b", r"--pr-budget")

    def test_slices_respect_the_budget_and_the_final_review_leaves_a_guide(self):
        for name, sec in agent_sections(self, "architect", "## Rules").items():
            self.assert_has(name, sec, r"slice order", r"review budget", r"split", r"before the human gate")
        for name, sec in agent_sections(self, "reviewer", "## Rounds").items():
            self.assert_has(name, sec, GUIDE, *GUIDE_HEADINGS)
        for name, sec in instr_sections(self, H_PRS).items():
            self.assert_has(name, sec, GUIDE, r"Where to look", r"project terms")

    def test_docs_describe_per_slice_prs_budget_and_guide_and_instructions_fit_the_cap(self):
        self.assert_has("docs/workflow", read(WF), r"each slice", r"own PR", r"\b400\b", r"--pr-budget",
                        r"TEAM_PR_BUDGET", GUIDE)
        self.assert_has("docs/guard-rails", section(read(GUARD_RAILS), "## Record routing and merge risk"),
                        r"--pr-budget", r"TEAM_PR_BUDGET", r"Review-size warning:", r"over_budget", r"review_lines")
        for name, path in INSTR.items():
            with self.subTest(file=name):
                self.assertLessEqual(path.stat().st_size, 32768)


class TestGoalLoopRemoved(RulesCase):

    def test_no_goal_wording_remains_in_instructions_adapters_or_docs(self):
        files = [INSTR["claude-code"], INSTR["codex"], README] + sorted((ROOT / "docs").glob("*.md"))
        adapters = sorted((ROOT / "adapters").glob("*/adapter.json"))
        self.assertEqual(len(adapters), 2, adapters)
        for path in files + adapters:
            with self.subTest(file=str(path.relative_to(ROOT))):
                self.assertNotRegex(read(path), re.compile(r"goal", re.I))
        for path in adapters:
            with self.subTest(adapter=str(path.relative_to(ROOT))):
                self.assertNotIn("goal_mechanics", json.loads(read(path)).get("vars", {}))


class TestFooterSilentOnSize(RulesCase):

    def test_footer_rules_count_the_review_budget_and_say_size_is_never_named(self):
        secs = dict(instr_sections(self, H_FOOTER))
        secs["docs/workflow"] = section(read(WF), "## Merge-risk footer")
        for name, sec in secs.items():
            self.assert_has(name, sec, r"review budget", r"never names size")
            self.assert_lacks(name, sec, r"500 changed non-test lines")


class TestOverBudgetException(ToolCase, RulesCase):

    def test_over_budget_slice_needs_a_stated_exception_or_the_humans_approval(self):
        for name, doc in agent_texts("architect").items():
            slices = template_section(doc, "## Slice order (Full only)")
            rules = section(doc, "## Rules")
            self.assertIsNotNone(slices, "%s has no slice order template" % name)
            self.assertIsNotNone(rules, "%s has no rules" % name)
            self.assert_has(name, slices, r"estimated review size")
            self.assert_has(name, slices + "\n" + rules, r"coherent vertical", r"why it can.t be split",
                            r"exception")
        for name, sec in instr_sections(self, H_LEAD).items():
            self.assert_has(name, sec, r"over budget", r"approve over budget", re.escape(APPROVAL_STEP),
                            r"back to the architect")

        ws = self.new_ws("f")
        self.ok(self.sb.tg("routing", "record", "route", "--proposed", "full", "--confirmed", "full", cwd=ws))
        self.ok(self.sb.tg("routing", "record", "step", "--step", "over-budget-approved", "--seconds", "0", cwd=ws))
        steps = [e for e in self.log_lines() if e.get("event") == "step"]
        self.assertEqual([(e["step"], e["seconds"]) for e in steps], [("over-budget-approved", 0)])


if __name__ == "__main__":
    unittest.main()
