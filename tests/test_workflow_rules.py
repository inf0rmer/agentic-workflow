"""Checks on the rendered workflow instructions, agent files and user docs.

Each test reads committed text (dist/*, docs/workflow.md, README.md) and asserts
that an agent following it would pick a workflow variant, pass design gates,
write and review tests, and report merge risk the way the workflow intends.
Patterns are case-insensitive regexes unless a check says the text is exact.
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
H_GATES = "### Design gates"
H_CRIT = "### Crit at a gate"
H_FALLBACK = "### Gate fallback without Crit"
H_DOUBLE = "### Double-loop tests"
H_MUTATION = "### Mutation testing"
H_GOAL = "### Keep the loop running with `/goal`"
H_ISOLATION = "### Context isolation"
H_FOOTER = "### Merge-risk footer"

VARIANTS_HEADER = "| | Chore | Bug fix | Standard | Full |"
VARIANTS_ROWS = [
    ["For", "docs, bumps, renames, no behavior change", "a defect with a repro, no risk flags",
     "a new or changed behavior, one repo, no risk flags",
     "several repos, public contract, persisted data, security, cross-team, hard to revert"],
    ["Workspace", "no", "yes", "yes", "yes"],
    ["Spec / criteria", "checklist of ≤3 items", "lead writes it, 1–5", "PO, 5–20",
     "PO, 20–40 per slice (split past 40)"],
    ["Design gate", "–", "–", "light (D2)", "full (D1 + D2)"],
    ["Isolated tester, sealed tests", "–", "regression test only", "yes", "yes"],
    ["Holdouts", "0", "0", "2–3", "3–8"],
    ["Mutation testing", "off", "opt-in", "opt-in, sampled, ≤15 min", "on for risk-flagged modules, sampled"],
    ["Review rounds", "1", "1 (2 if blockers)", "≤2", "≤2"],
    ["Human code review", "always", "always", "always", "always"],
]

CHECKLIST_TOPICS = [r"repos?", r"files", r"public contract", r"persisted data", r"security", r"cross-team",
                    r"revert", r"user", r"design question", r"repro"]

RULES_HEADER = "| # | Condition | Variant |"
RULES_VARIANTS = ["Full", "Full", "Standard", "Bug fix", "Chore", "Standard"]

STOP_SET = [r"review round 2", r"DEFER", r"security question", r"human decision", r"\d+ turns"]
GOALS = [
    ("**D1 goal (Full):**", [r"Stop at the D1 gate", r"Never push"], [r"D2", r"seal", r"human gate"]),
    ("**D2 goal (Standard, Full):**", [r"Stop at the D2 gate", r"Never push"],
     [r"human gate", r"sealed tests pass", r"Phase 3"]),
    ("**Human-gate goal (Bug fix):**", [r"Stop at the human gate", r"Never push"] + STOP_SET, []),
    ("**Human-gate goal (Standard):**", [r"Stop at the human gate", r"Never push"] + STOP_SET, []),
    ("**Human-gate goal (Full):**", [r"Stop at the human gate", r"Never push"] + STOP_SET, []),
]

FOOTER_LINE = r"Merge risk: (high|medium|low) \(advisory\) — \S"
PLAIN_WORDS_BANNED = [r"sealed", r"holdout", r"Jev", r"mutation survivors"]


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
    """Rows (lists of trimmed cells) of the Markdown table whose header line is exactly `header`, or None."""
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


def numbered_lists(doc):
    """Top-level numbered lists: each a list of item texts (item line plus its indented continuation)."""
    lists, cur = [], None
    for line in doc.splitlines():
        m = re.match(r"^(\d+)\.\s+(.*)$", line)
        if m:
            if cur is None or m.group(1) == "1":
                cur = []
                lists.append(cur)
            cur.append((int(m.group(1)), m.group(2)))
        elif cur is not None and (line.startswith((" ", "\t")) and line.strip()):
            n, t = cur[-1]
            cur[-1] = (n, t + " " + line.strip())
        elif cur is not None and line.strip():
            cur = None
    return lists


def checklist(doc):
    """The ten-item numbered list in `doc`, as item texts, or None."""
    for lst in numbered_lists(doc):
        if len(lst) == 10:
            return lst
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

    def instr_section(self, heading):
        out = {}
        for name, doc in instr_texts().items():
            sec = section(doc, heading)
            self.assertIsNotNone(sec, "%s has no %r heading" % (name, heading))
            out[name] = sec
        return out

    def goal_blocks(self, sec):
        """{label: body} for fenced blocks starting with /goal, with the exact label line preceding each."""
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

    def test_instructions_define_four_workflow_variants(self):
        """Both instruction files carry the four-variant table with the agreed values, and drop the
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
            self.assert_has(name, doc, r"Bug fix.{0,40}\b0\b", r"Standard.{0,40}2[–-]3",
                            r"Full.{0,40}3[–-]8")
            self.assert_lacks(name, doc, r"write 3[–-]8")

    def test_routing_checklist_has_ten_questions_answered_without_code(self):
        for name, sec in self.instr_section(H_CHOOSING).items():
            with self.subTest(file=name):
                items = checklist(sec)
                self.assertIsNotNone(items, "%s: no numbered list of exactly ten items" % name)
                self.assertEqual([n for n, _ in items], list(range(1, 11)))
                for (n, item), topic in zip(items, CHECKLIST_TOPICS):
                    with self.subTest(item=n):
                        self.assertRegex(item, re.compile(topic, re.I))
                self.assert_has(name, sec, r"without reading (any )?code")

    def test_rule_table_maps_answers_to_variant_first_match_wins(self):
        for name, sec in self.instr_section(H_CHOOSING).items():
            with self.subTest(file=name):
                rows, _ = table_after(sec, RULES_HEADER)
                self.assertIsNotNone(rows, "%s: no table with header %r" % (name, RULES_HEADER))
                self.assertEqual(len(rows), 6, "rule table must have six rows")
                self.assertEqual([r[0] for r in rows], [str(i) for i in range(1, 7)])
                self.assertEqual([r[-1].replace("**", "").strip() for r in rows], RULES_VARIANTS)
                self.assert_has(name, sec, r"top-down", r"first match wins", r'unknown answer counts as "?yes"?')

    def test_human_confirms_variant_and_it_only_moves_up(self):
        for name, sec in self.instr_section(H_CHOOSING).items():
            self.assert_has(name, sec, r"confirm(s)? or override(s)?", r"one line", r"Jev does not route",
                            r"upgraded mid-run", r"never downgraded", r"design question.{0,80}at least Standard")

    def test_routing_is_recorded_in_workspace_and_shared_log_is_only_planned(self):
        for name, sec in self.instr_section(H_CHOOSING).items():
            self.assert_has(name, sec, r"routing\.md", r"~/\.agentic-workflow/routing-log\.jsonl", r"planned",
                            r"no tool writes it yet")
        for name, doc in instr_texts().items():
            self.assert_lacks(name, doc, r"team-guard (route|routing|log)", r"routing-log (append|write)")


# ---------------------------------------------------------------- design gates

class TestDesignGates(RulesCase):

    def test_instructions_describe_d1_and_d2_gates_run_by_lead(self):
        for name, sec in self.instr_section(H_GATES).items():
            self.assert_has(name, sec, r"D1", r"Full only", r"options brief", r"adr/", r"ADR", r"D2",
                            r"Standard and Full", r"contract\.md", r"before (the )?stubs", r"lead runs both gates",
                            r"nothing is sealed until D2 passes")

    def test_gate_passes_on_zero_comments_and_records_file_hashes(self):
        for name, sec in self.instr_section(H_GATES).items():
            self.assert_has(name, sec, r"zero comments", r"design-gate\.md", r"sha256", r"D2 reopens")

    def test_crit_review_stays_local_and_comments_are_exported(self):
        for name, sec in self.instr_section(H_CRIT).items():
            self.assert_has(name, sec, r"<workspace>/\.crit", r"CRIT_NO_UPDATE_CHECK=1", r"owner", r"share", r"push",
                            r"pull", r"--pr", r"never", r"reviews/", r"before re-running", r"approval deletes")
        for name, doc in rendered_texts().items():
            self.assert_lacks(name, doc, r"team-guard crit")
            for info, body, _, _ in fenced_blocks(doc):
                for line in body.splitlines():
                    with self.subTest(file=name, line=line):
                        self.assertNotRegex(line, re.compile(r"^\s*(\$\s*)?crit\s", re.I),
                                            "%s has a Crit command in a code block" % name)

    def test_tester_never_sees_review_comments(self):
        for name, doc in agent_texts("tester").items():
            self.assert_has(name, doc, r"\.crit/", r"reviews/", r"never", r"approved state")
        for name, sec in self.instr_section(H_ISOLATION).items():
            self.assert_has(name, sec, r"\.crit/", r"reviews/", r"never")

    def test_gate_fallback_needs_literal_approval_reply(self):
        for name, sec in self.instr_section(H_FALLBACK).items():
            self.assert_has(name, sec, r"Codex", r"numbered questions", r"no other reply counts as approval")
            for exact in ("approve D1", "approve D2"):
                with self.subTest(file=name, exact=exact):
                    self.assertIn(exact, sec)


# ---------------------------------------------------------------- double loop and self-contained code

class TestDoubleLoopAndSelfContainedCode(RulesCase):

    def test_tester_writes_one_sealed_test_per_criterion_and_engineer_owns_inner_loop(self):
        for name, doc in agent_texts("tester").items():
            self.assert_has(name, doc, r"one (sealed )?test per criterion", r"contract seam",
                            r"second test only for a documented error( case)? or a stated invariant",
                            r"end-to-end tests are not a default")
            self.assert_lacks(name, doc, r"acceptance, integration,? and e2e", r"acceptance/integration/e2e")
        for name, doc in agent_texts("software-engineer").items():
            self.assert_has(name, doc, r"inner loop", r"red-green", r"unit tests", r"not sealed", r"not evidence")
        for name, sec in self.instr_section(H_DOUBLE).items():
            self.assert_has(name, sec, r"per slice", r"single cycle", r"one sealed regression test")
        for name, doc in instr_texts().items():
            self.assert_lacks(name, doc, r"acceptance/integration/e2e")

    def test_reviewer_flags_tautological_and_self_mocking_unit_tests(self):
        for name, doc in agent_texts("reviewer").items():
            self.assert_has(name, doc, r"tautolog", r"mocks? of the project's own modules", r"finding")

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
        for name, doc in rendered_texts().items():
            self.assert_lacks(name, doc, r"team-guard refs")


# ---------------------------------------------------------------- mutation, docs, goals, footer

class TestMutationDocsGoalsFooter(RulesCase):

    def test_mutation_testing_is_sampled_and_time_boxed_by_variant(self):
        for name, sec in self.instr_section(H_MUTATION).items():
            self.assert_has(name, sec, r"Chore.{0,40}off", r"Bug fix.{0,40}opt-in", r"Standard.{0,80}15 min",
                            r"Full.{0,80}risk-flagged", r"sampled", r"time-boxed")
        for name, doc in instr_texts().items():
            self.assert_lacks(name, doc, r"kill or justify every", r"killed or justified")
        for name, doc in agent_texts("tester").items():
            self.assert_has(name, doc, r"sampled", r"time-boxed")
            self.assert_lacks(name, doc, r"kill or justify every", r"killed or justified")
        for name, doc in agent_texts("reviewer").items():
            self.assert_has(name, doc, r"sampled", r"time-boxed")
            self.assert_lacks(name, doc, r"killed or justified")
        wf = read(WF)
        self.assert_has("docs/workflow.md", wf, r"Python has no supported mutation path")
        self.assert_lacks("docs/workflow.md", wf, r"kills or justifies every")

    def test_doc_writer_cites_a_source_for_every_fact(self):
        for name, doc in agent_texts("doc-writer").items():
            self.assert_has(name, doc, r"cit(e|ed) (a )?source for every fact", r"unverified")

    def test_one_goal_per_gate_each_stopping_at_its_gate(self):
        for name, sec in self.instr_section(H_GOAL).items():
            with self.subTest(file=name):
                text_blocks = [b for b in fenced_blocks(sec) if b[0] == "text"]
                self.assertEqual(len(text_blocks), 5, "%s: expected five ```text blocks" % name)
                found = self.goal_blocks(sec)
                self.assertEqual(sum(len(v) for v in found.values()), 5,
                                 "%s: expected exactly five /goal blocks, got labels %r" % (name, list(found)))
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
                            r"after the human approves the Phase 4 summary", r'unknown input(s)? count(s)? as "?yes"?',
                            r"only raise", r"review findings", r"unseals", r"skipped mutation testing", r"planned",
                            r"later script")
            with self.subTest(file=name):
                examples = [l for l in sec.splitlines() if re.search(FOOTER_LINE, l, re.I)]
                self.assertTrue(examples, "%s: no footer example line matching %r" % (name, FOOTER_LINE))
                for line in examples:
                    self.assert_lacks("%s footer example" % name, line, *PLAIN_WORDS_BANNED)


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

    def test_workflow_docs_describe_variants_gates_and_readme_row(self):
        wf = read(WF)
        for h in ("## Choose a variant", "## Design gates", "## Double-loop tests", "## Mutation testing",
                  "## Merge-risk footer"):
            with self.subTest(heading=h):
                self.assertIsNotNone(section(wf, h), "docs/workflow.md has no %r heading" % h)
        self.assert_lacks("docs/workflow.md", wf, r"might only need an engineer and a tester")
        body = mermaid_flowchart(wf)
        self.assertIsNotNone(body, "docs/workflow.md has no mermaid flowchart")
        defined, used, labels = parse_flowchart(body)
        self.assertEqual(sorted(used - defined), [], "edges reference undefined nodes")
        for word in ("D1", "D2", "variant"):
            with self.subTest(label=word):
                self.assertTrue(any(re.search(word, l, re.I) for l in labels), "no node label mentions %s" % word)
        self.assert_has("phase diagram", body, r"Chore", r"Bug fix", r"Standard", r"Full")
        rows = [l for l in read(README).splitlines() if l.strip().startswith("|")]
        self.assertFalse([r for r in rows if re.search(r"ritual", r, re.I)], "README still has a 'ritual' row")
        first_cells = [r.strip().strip("|").split("|")[0] for r in rows]
        self.assertTrue(any(re.search(r"inner loop", c, re.I) and re.search(r"one sealed test per criterion", c, re.I)
                            for c in first_cells), "README has no row about the inner loop under sealed tests")

    def test_instruction_files_fit_codex_size_cap(self):
        for name, path in INSTR.items():
            with self.subTest(file=name):
                self.assertLessEqual(path.stat().st_size, 32768)

    def test_committed_dist_matches_build_and_has_no_placeholders(self):
        """The existing self-tests are run by tests/run.sh itself, which runs this file."""
        for harness in ("claude-code", "codex"):
            with self.subTest(harness=harness):
                p = subprocess.run([sys.executable, str(ROOT / "build.py"), "--check", harness], cwd=ROOT,
                                   capture_output=True, text=True, timeout=120)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        for harness in ("claude-code", "codex"):
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
        cc, cx = docs["claude-code"], docs["codex"]

        def parts(doc):
            choosing = section(doc, H_CHOOSING) or ""
            goal = section(doc, H_GOAL) or ""
            return {
                "variants table": table_after(section(doc, H_VARIANTS) or "", VARIANTS_HEADER)[1],
                "rule table": table_after(choosing, RULES_HEADER)[1],
                "checklist": checklist(choosing),
                "goal blocks": self.goal_blocks(goal) or None,
                "design gates": section(doc, H_GATES),
                "merge-risk footer": section(doc, H_FOOTER),
            }

        a, b = parts(cc), parts(cx)
        for key in a:
            with self.subTest(part=key):
                self.assertIsNotNone(a[key], "claude-code has no %s" % key)
                self.assertIsNotNone(b[key], "codex has no %s" % key)
                self.assertEqual(a[key], b[key])


if __name__ == "__main__":
    unittest.main()
