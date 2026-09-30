"""Checks on the rendered workflow instructions, agent files and user docs.

Each test reads committed text (dist/*, docs/workflow.md, README.md) and asserts
that an agent following it would pick a workflow variant, pass the design gate,
write and review tests, work at speed, and report merge risk the way the workflow
intends. Patterns are case-insensitive regexes unless a check says the text is exact.
"""

import re
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
INSTR = {"claude-code": DIST / "claude-code" / "CLAUDE.md",
         "codex": DIST / "codex" / "AGENTS.md"}
ROLES = ("architect", "doc-writer", "product-owner", "researcher", "reviewer", "software-engineer", "tester")
WF = ROOT / "docs" / "workflow.md"
README = ROOT / "README.md"

H_VARIANTS = "### Workflow variants"
H_CHOOSING = "### Choosing a variant"
H_STEPS = "### Steps by variant"
H_GATE = "### Design gate"
H_CRIT = "### Crit at a gate"
H_FALLBACK = "### Gate fallback without Crit"
H_DOUBLE = "### Double-loop tests"
H_MUTATION = "### Mutation testing"
H_SPEED = "### Speed rules"
H_GOAL = "### Keep the loop running with `/goal`"
H_ISOLATION = "### Context isolation"
H_FOOTER = "### Merge-risk footer"

EN, LE = "–", "≤"
VARIANTS_HEADER = "| | Chore | Bug fix | Spike | Standard | Full |"
VARIANTS_ROWS = [
    ["For", "docs, bumps, renames, no behavior change", "a defect with a repro, no risk flags",
     "exploration: \"can this work?\" or \"what's the shape of it?\"",
     "a new or changed behavior, one repo, no risk flags",
     "several repos, public contract, persisted data, security, cross-team, hard to revert"],
    ["Workspace", "no", "yes", "yes", "yes", "yes"],
    ["Spec / criteria", "checklist of %s3 items" % LE, "lead writes it, 1%s5" % EN, "none; a time box",
     "PO, 5%s20" % EN, "PO, 20%s40 per slice (split past 40)" % EN],
    ["Design gate", EN, EN, EN, "one gate", "one gate (D1 + D2 if split)"],
    ["Isolated tester, sealed tests", EN, "regression test only", EN, "yes", "yes"],
    ["Holdouts", "0", "0", "0", "1%s2" % EN, "2%s4" % EN],
    ["Mutation testing", "off", "off", "off", "off", "off; opt-in for risk-flagged modules"],
    ["Review rounds", "1", "1 (2 if blockers)", "1", "1 + fix re-check", "1 + fix re-check"],
    ["Human code review", "always", "always", "always", "always", "always"],
]

CHECKLIST_TOPICS = [r"repos?", r"files", r"public contract", r"persisted data", r"security", r"cross-team",
                    r"revert", r"user", r"design question", r"repro"]

RULES_HEADER = "| # | Condition | Variant |"
RULES_VARIANTS = ["Spike", "Full", "Full", "Standard", "Bug fix", "Chore", "Standard"]

FULL_STEPS_LABEL = "**Full (5 steps):**"
STANDARD_STEPS_LABEL = "**Standard (5 steps):**"
FULL_STEP_TOPICS = [[r"design", r"product-owner", r"architect"], [r"design gate"], [r"tests?", r"scaffold"],
                    [r"build"], [r"fresh review", r"re-check"]]

STOP_SET = [r"blockers.{0,60}re-check", r"DEFER", r"security question", r"human decision", r"\d+ turns"]
HUMAN_GATE_MUST = [r"Stop at the human gate", r"Never push"] + STOP_SET
GOALS = [
    ("**Design-gate goal (Standard, Full):**", [r"Stop at the design gate", r"Never push"],
     [r"human gate", r"sealed tests pass"]),
    ("**D1 goal (split gates):**", [r"Stop at the D1 gate", r"Never push"], [r"D2", r"seal", r"human gate"]),
    ("**D2 goal (split gates):**", [r"Stop at the D2 gate", r"Never push"], [r"human gate", r"sealed tests pass"]),
    ("**Human-gate goal (Bug fix):**", HUMAN_GATE_MUST, []),
    ("**Human-gate goal (Standard):**", HUMAN_GATE_MUST, []),
    ("**Human-gate goal (Full):**", HUMAN_GATE_MUST, []),
]

FOOTER_LINE = r"Merge risk: (high|medium|low) \(advisory\) — \S"
PLAIN_WORDS_BANNED = [r"sealed", r"holdout", r"Jev", r"mutation survivors"]

# Something that does not exist yet: any line naming it must also say it is planned.
PLANNED_GRILLING = [r"grilling"]
# Tools that now exist: no line naming one may still call it planned.
AVAILABLE_ROUTING_LOG = [r"routing-log\.jsonl"]
AVAILABLE_MUTATION_LIMITS = [r"mutation-check.{0,80}(disk|concurrency|clean)"]
AVAILABLE_RESIGN = [r"team-guard sign", r"re-sign command"]
AVAILABLE_RISK_SCRIPT = [r"merge-risk.{0,40}script"]


# ---------------------------------------------------------------- helpers

def read(path):
    return Path(path).read_text(encoding="utf-8")


def agent_texts(role):
    """The instruction text an agent of `role` reads, per harness (TOML: its developer_instructions)."""
    md = DIST / "claude-code" / "agents" / ("%s.md" % role)
    toml = DIST / "codex" / "agents" / ("%s.toml" % role)
    with open(toml, "rb") as f:
        codex = tomllib.load(f)["developer_instructions"]
    return {"claude-code/%s.md" % role: read(md), "codex/%s.toml" % role: codex}


def instr_texts():
    return {name: read(path) for name, path in INSTR.items()}


def rendered_texts():
    """Both instruction files and every role's agent file in both harnesses."""
    out = {"instructions/" + k: v for k, v in instr_texts().items()}
    for role in ROLES:
        out.update(agent_texts(role))
    return out


def fence_mask(lines):
    """For each line, whether it sits inside (or opens/closes) a fenced code block."""
    mask, inside = [], False
    for line in lines:
        if line.lstrip().startswith("```"):
            mask.append(True)
            inside = not inside
        else:
            mask.append(inside)
    return mask


def section(doc, heading):
    """Text from the exact heading line to the next heading of the same or higher level, or None."""
    lines = doc.splitlines()
    mask = fence_mask(lines)
    level = len(heading) - len(heading.lstrip("#"))
    for i, line in enumerate(lines):
        if not mask[i] and line.rstrip() == heading:
            out = [line]
            for j in range(i + 1, len(lines)):
                m = re.match(r"^(#+)\s", lines[j])
                if not mask[j] and m and len(m.group(1)) <= level:
                    break
                out.append(lines[j])
            return "\n".join(out)
    return None


def fenced_blocks(doc):
    """(info string, body, index of the opening line, all lines) for every fenced block."""
    lines = doc.splitlines()
    blocks, i = [], 0
    while i < len(lines):
        m = re.match(r"^\s*```(.*)$", lines[i])
        if m:
            start, info, body = i, m.group(1).strip(), []
            i += 1
            while i < len(lines) and not lines[i].lstrip().startswith("```"):
                body.append(lines[i])
                i += 1
            blocks.append((info, "\n".join(body), start, lines))
        i += 1
    return blocks


def table_after(doc, header):
    """(rows of trimmed cells, raw table text) for the table whose header line is exactly `header`."""
    lines = doc.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == header:
            rows = []
            for row in lines[i + 2:]:
                if not row.strip().startswith("|"):
                    break
                rows.append([c.strip() for c in row.strip().strip("|").split("|")])
            return rows, "\n".join(lines[i:i + 2 + len(rows)])
    return None, None


def read_list(lines, start):
    """The numbered list starting at line `start`: [(number, item text with continuations)]."""
    items = []
    for line in lines[start:]:
        m = re.match(r"^(\d+)\.\s+(.*)$", line)
        if m:
            if items and m.group(1) == "1":
                break
            items.append((int(m.group(1)), m.group(2)))
        elif items and line.startswith((" ", "\t")) and line.strip():
            n, t = items[-1]
            items[-1] = (n, t + " " + line.strip())
        elif line.strip():
            break
    return items


def numbered_lists(doc):
    lines = doc.splitlines()
    return [read_list(lines, i) for i, line in enumerate(lines) if re.match(r"^1\.\s", line)]


def checklist(doc):
    """The ten-item numbered list in `doc`, or None."""
    for lst in numbered_lists(doc):
        if len(lst) == 10:
            return lst
    return None


def list_under(doc, label):
    """The numbered list directly after the exact label line (blank lines ignored), or None."""
    lines = doc.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == label:
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and re.match(r"^1\.\s", lines[j]):
                return read_list(lines, j)
            return None
    return None


class RulesCase(unittest.TestCase):
    maxDiff = None

    def assert_has(self, name, text, *patterns):
        self.assertIsNotNone(text, "%s: section or file is missing" % name)
        for p in patterns:
            with self.subTest(file=name, pattern=p):
                self.assertRegex(text, re.compile(p, re.I), "%s must match %r" % (name, p))

    def assert_lacks(self, name, text, *patterns):
        for p in patterns:
            with self.subTest(file=name, absent=p):
                self.assertNotRegex(text, re.compile(p, re.I), "%s must not match %r" % (name, p))

    def assert_only_planned(self, patterns):
        """Every rendered line naming a not-yet-built tool also calls it planned."""
        for name, doc in rendered_texts().items():
            for line in doc.splitlines():
                for p in patterns:
                    if re.search(p, line, re.I):
                        with self.subTest(file=name, line=line.strip()[:120]):
                            self.assertRegex(line, re.compile(r"planned", re.I),
                                             "%s presents %r as existing" % (name, p))

    def assert_not_planned(self, patterns):
        """No rendered line naming a tool that now exists still calls it planned."""
        for name, doc in rendered_texts().items():
            for line in doc.splitlines():
                for p in patterns:
                    if re.search(p, line, re.I):
                        with self.subTest(file=name, line=line.strip()[:120]):
                            self.assertNotRegex(line, re.compile(r"planned", re.I),
                                                "%s still calls %r planned" % (name, p))

    def instr_section(self, heading):
        out = {}
        for name, doc in instr_texts().items():
            sec = section(doc, heading)
            self.assertIsNotNone(sec, "%s has no %r heading" % (name, heading))
            out[name] = sec
        return out

    def goal_blocks(self, sec):
        """{label: [(info, body)]} for fenced blocks starting with /goal, keyed by the line before each."""
        found = {}
        for info, body, start, lines in fenced_blocks(sec):
            if not body.lstrip().startswith("/goal"):
                continue
            j = start - 1
            while j >= 0 and not lines[j].strip():
                j -= 1
            found.setdefault(lines[j].strip() if j >= 0 else "", []).append((info, body))
        return found


# ---------------------------------------------------------------- variants and routing

class TestVariantsAndRouting(RulesCase):

    def test_instructions_define_five_workflow_variants(self):
        """Both instruction files carry the five-variant table with the agreed values and drop the
        old ad hoc team-scaling advice; the tester's holdout count follows the variant."""
        for name, sec in self.instr_section(H_VARIANTS).items():
            with self.subTest(file=name):
                rows, _ = table_after(sec, VARIANTS_HEADER)
                self.assertIsNotNone(rows, "%s: no table with header %r" % (name, VARIANTS_HEADER))
                self.assertEqual(rows, VARIANTS_ROWS)
        for name, doc in instr_texts().items():
            self.assertIsNone(section(doc, "### Task sizing"), "%s still has a Task sizing section" % name)
            self.assert_lacks(name, doc, r"Scale the team to the task", r"Not every feature needs all agents",
                              r"Task sizing", r"5-6 tasks")
        for name, doc in agent_texts("tester").items():
            self.assert_has(name, doc, r"Bug fix.{0,40}\b0\b", r"Standard.{0,40}1[–-]2",
                            r"Full.{0,40}2[–-]4")
            self.assert_lacks(name, doc, r"write 3[–-]8")

    def test_spike_is_time_boxed_exploration_that_never_merges(self):
        for name, sec in self.instr_section(H_VARIANTS).items():
            self.assert_has(name, sec, r"time-box", r"one engineer", r"findings\.md", r"never merges as-is",
                            r"re-run as Standard or Full", r"no spec")
        for name, sec in self.instr_section(H_CHOOSING).items():
            with self.subTest(file=name):
                rows, _ = table_after(sec, RULES_HEADER)
                self.assertTrue(rows, "%s: no table with header %r" % (name, RULES_HEADER))
                self.assertEqual(rows[0][-1].replace("**", "").strip(), "Spike")
                self.assert_has(name + " rule 1", rows[0][1], r"can this work", r"shape")

    def test_routing_checklist_and_rule_table_route_without_reading_code(self):
        for name, sec in self.instr_section(H_CHOOSING).items():
            with self.subTest(file=name):
                items = checklist(sec)
                self.assertIsNotNone(items, "%s: no numbered list of exactly ten items" % name)
                self.assertEqual([n for n, _ in items], list(range(1, 11)))
                for (n, item), topic in zip(items, CHECKLIST_TOPICS):
                    with self.subTest(item=n):
                        self.assertRegex(item, re.compile(topic, re.I))
                rows, _ = table_after(sec, RULES_HEADER)
                self.assertIsNotNone(rows, "%s: no table with header %r" % (name, RULES_HEADER))
                self.assertEqual(len(rows), 7, "rule table must have seven rows")
                self.assertEqual([r[0] for r in rows], [str(i) for i in range(1, 8)])
                self.assertEqual([r[-1].replace("**", "").strip() for r in rows], RULES_VARIANTS)
                self.assert_has(name, sec, r"without reading (any )?code", r"top-down", r"first match wins",
                                r'unknown answer counts as "?yes"?')

    def test_human_confirms_variant_and_it_only_moves_up(self):
        for name, sec in self.instr_section(H_CHOOSING).items():
            self.assert_has(name, sec, r"confirm(s)? or override(s)?", r"one line", r"Jev does not route",
                            r"upgraded mid-run", r"never downgraded", r"design question.{0,80}at least Standard")

    def test_routing_and_step_times_are_recorded_in_the_workspace_and_the_shared_log(self):
        for name, sec in self.instr_section(H_CHOOSING).items():
            self.assert_has(name, sec, r"routing\.md", r"wall time", r"~/\.agentic-workflow/routing-log\.jsonl",
                            r"team-guard routing record")
        for name, doc in instr_texts().items():
            self.assert_lacks(name, doc, r"routing-log (append|write)")
        self.assert_not_planned(AVAILABLE_ROUTING_LOG)


# ---------------------------------------------------------------- steps and design gate

class TestStepsAndDesignGate(RulesCase):

    def test_full_and_standard_run_five_steps_before_the_human_gate(self):
        for name, sec in self.instr_section(H_STEPS).items():
            with self.subTest(file=name):
                full = list_under(sec, FULL_STEPS_LABEL)
                self.assertIsNotNone(full, "%s: no numbered list under %r" % (name, FULL_STEPS_LABEL))
                self.assertEqual([n for n, _ in full], [1, 2, 3, 4, 5])
                for (n, item), topics in zip(full, FULL_STEP_TOPICS):
                    self.assert_has("%s Full step %d" % (name, n), item, *topics)
                std = list_under(sec, STANDARD_STEPS_LABEL)
                self.assertIsNotNone(std, "%s: no numbered list under %r" % (name, STANDARD_STEPS_LABEL))
                self.assertEqual([n for n, _ in std], [1, 2, 3, 4, 5])
                self.assert_has("%s Standard steps" % name, " ".join(t for _, t in std), r"lean contract")
                self.assert_has(name, sec, r"human gate", r"one upfront round of questions", r"options brief only if",
                                r"researcher is no longer a pipeline step", r"architect absorbs",
                                r"doc-writer.{0,60}only when user-facing docs change")
        for name, doc in list(instr_texts().items()) + [("docs/workflow.md", read(WF))]:
            self.assert_lacks(name, doc, r"4 steps", r"four steps")
        self.assert_only_planned(PLANNED_GRILLING)

    def test_e2e_tests_run_once_against_live_base_app_before_sealing(self):
        docs = dict(agent_texts("tester"))
        docs.update(instr_texts())
        for name, doc in docs.items():
            self.assert_has(name, doc, r"live base app", r"before sealing", r"feature missing", r"selector",
                            r"route", r"setup")

    def test_one_design_gate_splits_only_for_open_options_and_passes_on_zero_comments(self):
        for name, doc in instr_texts().items():
            self.assertIsNone(section(doc, "### Design gates"), "%s still has the plural gates heading" % name)
        for name, sec in self.instr_section(H_GATE).items():
            self.assert_has(name, sec, r"one design gate", r"contract\.md", r"brief", r"adr/", r"ADR", r"D1", r"D2",
                            r"separate reviews only when", r"nothing is sealed until the gate passes",
                            r"zero comments", r"design-gate\.md", r"sha256", r"reopens")

    def test_crit_review_stays_local_and_fallback_needs_literal_approval(self):
        for name, sec in self.instr_section(H_CRIT).items():
            self.assert_has(name, sec, r"<workspace>/\.crit", r"CRIT_NO_UPDATE_CHECK=1", r"owner", r"share", r"push",
                            r"pull", r"--pr", r"never", r"reviews/", r"before re-running", r"approval deletes")
        for name, sec in self.instr_section(H_FALLBACK).items():
            self.assert_has(name, sec, r"Codex", r"numbered questions", r"no other reply counts as approval")
            for exact in ("approve design", "approve D1", "approve D2"):
                with self.subTest(file=name, exact=exact):
                    self.assertIn(exact, sec)
        for name, doc in rendered_texts().items():
            self.assert_lacks(name, doc, r"team-guard crit")
            for _, body, _, _ in fenced_blocks(doc):
                for line in body.splitlines():
                    with self.subTest(file=name, line=line):
                        self.assertNotRegex(line, re.compile(r"^\s*(\$\s*)?crit\s", re.I),
                                            "%s has a Crit command in a code block" % name)

    def test_tester_never_sees_review_comments(self):
        for name, doc in agent_texts("tester").items():
            self.assert_has(name, doc, r"\.crit/", r"reviews/", r"never", r"approved state")
        for name, sec in self.instr_section(H_ISOLATION).items():
            self.assert_has(name, sec, r"\.crit/", r"reviews/", r"never")


# ---------------------------------------------------------------- test and code rules

class TestTestAndCodeRules(RulesCase):

    def test_one_sealed_test_per_criterion_with_engineer_inner_loop_reviewed(self):
        for name, doc in agent_texts("tester").items():
            self.assert_has(name, doc, r"one (sealed )?test per criterion", r"contract seam",
                            r"second test only for a documented error( case)? or a stated invariant",
                            r"end-to-end tests are not a default")
            self.assert_lacks(name, doc, r"acceptance, integration,? and e2e", r"acceptance/integration/e2e")
        for name, doc in agent_texts("software-engineer").items():
            self.assert_has(name, doc, r"inner loop", r"red-green", r"unit tests", r"not sealed", r"not evidence")
        for name, doc in agent_texts("reviewer").items():
            self.assert_has(name, doc, r"tautolog", r"mocks? of the project's own modules", r"finding")
        for name, sec in self.instr_section(H_DOUBLE).items():
            self.assert_has(name, sec, r"per slice", r"one cycle", r"one sealed regression test")
        for name, doc in instr_texts().items():
            self.assert_lacks(name, doc, r"acceptance/integration/e2e")

    def test_code_and_tests_never_cite_planning_documents(self):
        needles = [r"comments", r"docstrings", r"test names", r"file names", r"spec criterion IDs",
                   r"contract or architecture sections", r"addenda", r"review rounds"]
        for role in ("software-engineer", "tester", "reviewer"):
            for name, doc in agent_texts(role).items():
                sec = section(doc, "## Self-contained code")
                self.assertIsNotNone(sec, "%s has no '## Self-contained code' heading" % name)
                self.assert_has(name, sec, *needles)
                if role == "reviewer":
                    self.assert_has(name, sec, r"finding")
        for name, doc in agent_texts("reviewer").items():
            self.assert_has(name, doc, r"team-guard refs")

    def test_mutation_testing_is_off_by_default_and_sampled_when_on(self):
        for name, sec in self.instr_section(H_MUTATION).items():
            self.assert_has(name, sec, r"off by default", r"Full.{0,80}risk-flagged", r"sampled", r"time-boxed")
        for name, doc in agent_texts("tester").items():
            self.assert_has(name, doc, r"off by default", r"sampled", r"time-boxed")
        for name, doc in agent_texts("reviewer").items():
            self.assert_has(name, doc, r"sampled", r"time-boxed")
        docs = dict(instr_texts())
        docs.update(agent_texts("tester"))
        docs.update(agent_texts("reviewer"))
        for name, doc in docs.items():
            self.assert_lacks(name, doc, r"kill or justify every", r"kills or justifies", r"killed or justified")
        self.assert_not_planned(AVAILABLE_MUTATION_LIMITS)
        wf = read(WF)
        self.assert_has("docs/workflow.md", wf, r"Python has no supported mutation path")
        self.assert_lacks("docs/workflow.md", wf, r"kills or justifies every")

    def test_doc_writer_cites_a_source_for_every_fact(self):
        for name, doc in agent_texts("doc-writer").items():
            self.assert_has(name, doc, r"cit(e|ed) (a )?source for every fact", r"unverified")


# ---------------------------------------------------------------- speed rules, goals, footer

class TestSpeedGoalsFooter(RulesCase):

    def test_non_blocking_questions_wait_for_the_gate_and_one_subagent_runs_per_step(self):
        for name, sec in self.instr_section(H_SPEED).items():
            self.assert_has(name, sec, r"questions\.md", r"DEFER", r"PROVISIONAL", r"next gate",
                            r"only blocking or security questions", r"one subagent per step",
                            r"teammates.{0,80}only for parallel work within one step")
        cc = read(INSTR["claude-code"])
        self.assert_lacks("claude-code", cc, r"Use task dependencies to enforce phase ordering",
                          r"use \[Claude Code agent teams\]")

    def test_side_findings_are_deferred_env_stays_warm_and_commits_are_re_signed_at_the_gate(self):
        for name, sec in self.instr_section(H_SPEED).items():
            self.assert_has(name, sec, r"followups\.md", r"neither a blocker nor security",
                            r"stays running between rounds", r"config changes", r"unsigned", r"session environment",
                            r"never in repo or global config", r"re-sign", r"every commit on the branch",
                            r"before anything is pushed", r"team-guard sign")
        self.assert_not_planned(AVAILABLE_RESIGN)

    def test_one_goal_per_gate_each_stopping_at_its_gate(self):
        for name, sec in self.instr_section(H_GOAL).items():
            with self.subTest(file=name):
                text_blocks = [b for b in fenced_blocks(sec) if b[0] == "text"]
                self.assertEqual(len(text_blocks), 6, "%s: expected six ```text blocks" % name)
                found = self.goal_blocks(sec)
                self.assertEqual(sum(len(v) for v in found.values()), 6,
                                 "%s: expected exactly six /goal blocks, got labels %r" % (name, list(found)))
                for label, must, must_not in GOALS:
                    with self.subTest(file=name, goal=label):
                        self.assertIn(label, found, "%s: no /goal block after %r" % (name, label))
                        self.assertEqual(len(found[label]), 1)
                        info, body = found[label][0]
                        self.assertEqual(info, "text")
                        self.assert_has("%s %s" % (name, label), body, *must)
                        self.assert_lacks("%s %s" % (name, label), body, *must_not)

    def test_every_pr_gets_an_advisory_merge_risk_footer(self):
        for name, sec in self.instr_section(H_FOOTER).items():
            self.assert_has(name, sec, r"High", r"Medium", r"Low", r"advisory",
                            r"after the human approves the human-gate summary",
                            r'unknown input(s)? count(s)? as "?yes"?', r"only raise", r"review findings", r"unseals",
                            r"skipped mutation testing", r"team-guard risk")
            with self.subTest(file=name):
                examples = [l for l in sec.splitlines() if re.search(FOOTER_LINE, l, re.I)]
                self.assertTrue(examples, "%s: no footer example line matching %r" % (name, FOOTER_LINE))
                for line in examples:
                    self.assert_lacks("%s footer example" % name, line, *PLAIN_WORDS_BANNED)
        self.assert_not_planned(AVAILABLE_RISK_SCRIPT)


# ---------------------------------------------------------------- user docs, size, build

def mermaid_flowchart(doc):
    for info, body, _, _ in fenced_blocks(doc):
        if info == "mermaid" and body.lstrip().startswith("flowchart"):
            return body
    return None


NODE = re.compile(r"^\s*([A-Za-z0-9_]+)\s*(\[.*\]|\(.*\)|\{.*\})?\s*$")


def parse_flowchart(body):
    """(defined ids, ids used in edges, node labels) under the one-statement-per-line grammar."""
    defined, used, labels = set(), set(), []
    for line in body.splitlines()[1:]:
        if not line.strip() or line.strip().startswith("%%"):
            continue
        parts = re.split(r"-->(?:\|[^|]*\|)?", line)
        for part in parts:
            m = NODE.match(part)
            if not m:
                used.add("<unparsed: %s>" % part.strip())
                continue
            if m.group(2):
                defined.add(m.group(1))
                labels.append(m.group(2))
            if len(parts) > 1:
                used.add(m.group(1))
    return defined, used, labels


class TestDocsSizeBuild(RulesCase):

    def test_workflow_docs_describe_variants_and_gates_and_readme_row(self):
        wf = read(WF)
        for h in ("## Choose a variant", "## Steps by variant", "## Design gate", "## Double-loop tests",
                  "## Mutation testing", "## Merge-risk footer"):
            with self.subTest(heading=h):
                self.assertIsNotNone(section(wf, h), "docs/workflow.md has no %r heading" % h)
        self.assert_lacks("docs/workflow.md", wf, r"might only need an engineer and a tester")
        body = mermaid_flowchart(wf)
        self.assertIsNotNone(body, "docs/workflow.md has no mermaid flowchart")
        defined, used, labels = parse_flowchart(body)
        self.assertEqual(sorted(used - defined), [], "edges reference undefined nodes")
        for word in ("design gate", "human gate", "variant"):
            with self.subTest(label=word):
                self.assertTrue(any(re.search(word, l, re.I) for l in labels), "no node label mentions %s" % word)
        self.assert_has("phase diagram", body, r"Chore", r"Bug fix", r"Spike", r"Standard", r"Full")
        rows = [l for l in read(README).splitlines() if l.strip().startswith("|")]
        self.assertFalse([r for r in rows if re.search(r"ritual", r, re.I)], "README still has a 'ritual' row")
        first_cells = [r.strip().strip("|").split("|")[0] for r in rows]
        self.assertTrue(any(re.search(r"inner loop", c, re.I) and re.search(r"one sealed test per criterion", c, re.I)
                            for c in first_cells), "README has no row about the inner loop under sealed tests")

    def test_instructions_fit_size_cap_and_committed_dist_matches_build(self):
        """The existing self-tests are run by tests/run.sh itself, which also runs this file."""
        for name, path in INSTR.items():
            with self.subTest(file=name):
                self.assertLessEqual(path.stat().st_size, 32768)
        for harness in ("claude-code", "codex"):
            with self.subTest(harness=harness):
                p = subprocess.run([sys.executable, str(ROOT / "build.py"), "--check", harness], cwd=ROOT,
                                   capture_output=True, text=True, timeout=120)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            for f in sorted((DIST / harness).rglob("*")):
                if f.is_file():
                    with self.subTest(file=str(f.relative_to(ROOT))):
                        self.assertNotIn(b"{{", f.read_bytes())


# ---------------------------------------------------------------- invariants

class TestInvariants(RulesCase):

    def test_rendered_files_never_use_criterion_ids(self):
        for name, doc in rendered_texts().items():
            with self.subTest(file=name):
                self.assertNotRegex(doc, re.compile(r"\bAC-\d+"))

    def test_both_harnesses_render_identical_rules(self):
        docs = instr_texts()

        def parts(doc):
            choosing = section(doc, H_CHOOSING) or ""
            steps = section(doc, H_STEPS) or ""
            return {
                "variants table": table_after(section(doc, H_VARIANTS) or "", VARIANTS_HEADER)[1],
                "rule table": table_after(choosing, RULES_HEADER)[1],
                "checklist": checklist(choosing),
                "Full steps": list_under(steps, FULL_STEPS_LABEL),
                "Standard steps": list_under(steps, STANDARD_STEPS_LABEL),
                "goal blocks": self.goal_blocks(section(doc, H_GOAL) or "") or None,
                "design gate": section(doc, H_GATE),
                "speed rules": section(doc, H_SPEED),
                "merge-risk footer": section(doc, H_FOOTER),
            }

        a, b = parts(docs["claude-code"]), parts(docs["codex"])
        for key in a:
            with self.subTest(part=key):
                self.assertIsNotNone(a[key], "claude-code has no %s" % key)
                self.assertIsNotNone(b[key], "codex has no %s" % key)
                self.assertEqual(a[key], b[key])


if __name__ == "__main__":
    unittest.main()
