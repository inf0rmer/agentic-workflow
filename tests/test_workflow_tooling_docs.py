"""The rendered instructions and docs describe the workflow tooling as available.

Agents only use a tool the instructions tell them about, with the flags and exit codes
they need, and a tool described as "planned" is a tool nobody runs. Tools that still
don't exist keep that label.
"""

import re
import subprocess
import sys
import unittest
from pathlib import Path

from test_workflow_rules import (DIST, INSTR, ROLES, ROOT, WF, README, H_FOOTER, H_SPEED, agent_texts,
                                 instr_texts, read, section)

GUARD_RAILS = ROOT / "docs" / "guard-rails.md"
COMMANDS = ["team-guard refs", "team-guard routing record", "team-guard routing summary", "team-guard risk",
            "team-guard sign"]
NEEDLES = COMMANDS + ["--min-free-gb", "--max-concurrency", "--time-box", "--sample", "core.hooksPath"]
DEFAULTS = [r"--min-free-gb[^\n]{0,120}\b20\b", r"--max-concurrency[^\n]{0,120}\b2\b",
            r"--time-box[^\n]{0,120}\b15\b", r"--sample[^\n]{0,120}\b50\b"]
AVAILABLE = [r"team-guard (refs|routing|risk|sign|gate)", r"routing-log\.jsonl",
             r"mutation-check.{0,80}(disk|concurrency|clean)", r"--min-free-gb|--max-concurrency|--time-box|--sample",
             r"re-sign command", r"merge-risk.{0,40}script", r"core\.hooksPath"]
STILL_PLANNED = [r"seal.{0,80}gate record|gate record.{0,80}seal"]


def paragraphs(doc):
    return [p for p in re.split(r"\n\s*\n", doc) if p.strip()]


def docs():
    out = {"instructions/" + k: v for k, v in instr_texts().items()}
    for role in ROLES:
        out.update(agent_texts(role))
    out.update({"README.md": read(README), "docs/guard-rails.md": read(GUARD_RAILS), "docs/workflow.md": read(WF)})
    return out


class TestToolingDocs(unittest.TestCase):
    maxDiff = None

    def paragraph_with(self, name, doc, anchor, *patterns):
        hits = [p for p in paragraphs(doc) if re.search(anchor, p, re.I)
                and all(re.search(x, p, re.I) for x in patterns)]
        self.assertTrue(hits, "%s: no paragraph naming %r also matches %r" % (name, anchor, patterns))

    def test_docs_describe_the_workflow_tools_with_flags_defaults_and_exit_codes(self):
        everything = docs()
        union = "\n".join(everything.values())
        gr = read(GUARD_RAILS)
        for n in NEEDLES:
            with self.subTest(needle=n):
                self.assertIn(n, union)
                self.assertIn(n, gr, "docs/guard-rails.md describes %s" % n)
        for p in DEFAULTS:
            with self.subTest(default=p):
                self.assertRegex(gr, re.compile(p, re.I))
        self.paragraph_with("docs/guard-rails.md", gr, r"team-guard refs", r"exit\w*[^\n]{0,40}\b2\b")
        self.paragraph_with("docs/guard-rails.md", gr, r"mutation-check", r"exit\w*[^\n]{0,40}\b3\b")
        self.paragraph_with("docs/guard-rails.md", gr, r"mutation-check", r"exit\w*[^\n]{0,60}\b4\b")
        for name, doc in instr_texts().items():
            with self.subTest(file=name):
                for c in COMMANDS:
                    self.assertIn(c, doc)
                self.paragraph_with(name, doc, r"team-guard routing record", r"kickoff", r"after each step")
                self.paragraph_with(name, doc, r"team-guard risk", r"before", r"footer")
                self.assertLessEqual(len(doc.encode("utf-8")), 32768)
        for name, doc in agent_texts("reviewer").items():
            self.paragraph_with(name, doc, r"team-guard refs", r"before", r"diff")
        for name, doc in agent_texts("tester").items():
            with self.subTest(file=name):
                self.assertIn("--sample", doc)
                self.assertIn("--time-box", doc)
                self.paragraph_with(name, doc, r"exit", r"\b3\b", r"\b4\b")
        for name, doc in everything.items():
            for line in doc.splitlines():
                for p in AVAILABLE:
                    if re.search(p, line, re.I):
                        with self.subTest(file=name, line=line.strip()[:100]):
                            self.assertNotRegex(line, re.compile(r"planned", re.I))
                for p in STILL_PLANNED:
                    if re.search(p, line, re.I):
                        with self.subTest(file=name, still_planned=line.strip()[:100]):
                            self.assertRegex(line, re.compile(r"planned", re.I))
        for harness in ("claude-code", "codex"):
            with self.subTest(harness=harness):
                p = subprocess.run([sys.executable, str(ROOT / "build.py"), "--check", harness], cwd=ROOT,
                                   capture_output=True, text=True, timeout=120)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_footer_high_clause_names_a_breaking_contract_change(self):
        secs = {"instructions/" + k: section(v, H_FOOTER) for k, v in instr_texts().items()}
        secs["docs/workflow.md"] = section(read(WF), "## Merge-risk footer")
        for name, sec in secs.items():
            with self.subTest(file=name):
                self.assertIsNotNone(sec)
                self.assertIn("breaking public-contract change", sec)
                self.assertIn("additive public-contract change", sec)

    def test_the_lead_runs_sign_after_approval_and_the_human_gets_the_prompt(self):
        targets = {"instructions/" + k: section(v, H_SPEED) for k, v in instr_texts().items()}
        targets["docs/workflow.md"] = read(WF)
        targets["docs/guard-rails.md"] = read(GUARD_RAILS)
        for name, text in targets.items():
            with self.subTest(file=name):
                self.assertIsNotNone(text)
                self.paragraph_with(name, text, r"\blead\b[^.]*\bruns?\b[^.]*team-guard sign", r"approv",
                                    r"before (any|anything is) push")
                self.paragraph_with(name, text, r"team-guard sign|signing|sign(s|ed)?\b", r"prompt", r"\bhuman")
        rendered = dict(("instructions/" + k, v) for k, v in instr_texts().items())
        for role in ROLES:
            rendered.update(agent_texts(role))
        rendered.update({"README.md": read(README), "docs/workflow.md": read(WF), "docs/guard-rails.md": read(GUARD_RAILS)})
        for name, doc in rendered.items():
            with self.subTest(file=name):
                self.assertNotRegex(doc, re.compile(r"lead (only )?prepares|prepares the (sign )?command|"
                                                    r"never runs `?team-guard sign", re.I))

    def test_tester_role_has_no_phase_wording(self):
        texts = {"core/roles/tester.md": read(ROOT / "core" / "roles" / "tester.md")}
        texts.update(agent_texts("tester"))
        for name, text in texts.items():
            with self.subTest(file=name):
                self.assertNotIn("in this phase", text)

    def test_readme_states_the_codex_instruction_size(self):
        s = (DIST / "codex" / "AGENTS.md").stat().st_size
        m = re.search(r"At about (\d+(?:\.\d+)?) KB it leaves about (\d+(?:\.\d+)?) KB of Codex's 32 KiB cap",
                      read(README))
        self.assertIsNotNone(m, "README states the Codex instruction size")
        self.assertLessEqual(abs(float(m.group(1)) - s / 1024), 0.5)
        self.assertLessEqual(abs(float(m.group(2)) - (32768 - s) / 1024), 0.5)

    def test_help_names_both_harnesses_for_the_subagent_stop_hook(self):
        guard = ROOT / "core" / "bin" / "team-guard"
        p = subprocess.run([str(guard), "--help"], capture_output=True, text=True, timeout=60)
        lines = (p.stdout + p.stderr).splitlines()
        idx = [i for i, l in enumerate(lines) if "hook-stop" in l]
        self.assertTrue(idx, "help lists hook-stop")
        i = idx[0]
        block = [lines[i]] + [l for l in lines[i + 1:i + 3] if l.startswith("    ") and "team-guard " not in l]
        text = " ".join(block)
        for n in ("Claude Code", "Codex", "SubagentStop"):
            with self.subTest(needle=n):
                self.assertIn(n, text)


if __name__ == "__main__":
    unittest.main()
