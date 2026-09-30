"""The rendered instructions and docs run a design gate with the team-guard gate commands.

The lead follows the instructions literally, so they name each command with its flags and
exit codes, keep the chat fallback's exact replies, and say how to survive a stopped wait.
"""

import re
import subprocess
import sys
import unittest

from test_workflow_rules import (DIST, H_CRIT, H_FALLBACK, H_GATE, H_ISOLATION, INSTR, ROLES, ROOT, README, WF,
                                 agent_texts, instr_texts, read, section)

GUARD_RAILS = ROOT / "docs" / "guard-rails.md"


def paragraphs(text):
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


class TestGateDocs(unittest.TestCase):
    maxDiff = None

    def gate_text(self, doc):
        parts = [section(doc, h) for h in (H_GATE, H_CRIT, H_FALLBACK)]
        for h, p in zip((H_GATE, H_CRIT, H_FALLBACK), parts):
            self.assertIsNotNone(p, "missing %r" % h)
        return "\n\n".join(parts)

    def has_paragraph(self, name, text, *patterns):
        hits = [p for p in paragraphs(text) if all(re.search(x, p, re.I) for x in patterns)]
        self.assertTrue(hits, "%s: no paragraph matches all of %r" % (name, patterns))

    def test_instructions_run_the_gate_with_team_guard_gate_commands(self):
        for name, doc in instr_texts().items():
            with self.subTest(file=name):
                text = self.gate_text(doc)
                for needle in ("team-guard gate review", "team-guard gate reply", "team-guard gate record", "--crit",
                               "--chat", "team-guard gate check", "--new", "--no-open", "exit 3", "exit 2",
                               "exports each round", "before any further tests are sealed", "approve design",
                               "approve D1", "approve D2"):
                    self.assertIn(needle, text)
                self.assertRegex(text, r"gate record <gate> <files(…|\.\.\.)> --chat")
                self.has_paragraph(name, text, r"\breply\b", r"re-export")
                self.has_paragraph(name, text, r"exit 3", r"Codex", r"chat")
                self.has_paragraph(name, text, r"team-guard gate check", r"exit 2", r"reopen")
                self.has_paragraph(name, text, r"numbered questions", r"approve design")
                iso = section(doc, H_ISOLATION)
                self.assertIsNotNone(iso)
                for needle in (".crit/", "reviews/"):
                    self.assertIn(needle, iso)
                self.assertRegex(iso, re.compile(r"hook[^.]*den(y|ies)|den(y|ies)[^.]*hook", re.I))
                self.assertLessEqual(len(doc.encode("utf-8")), 32768)
        gr = read(GUARD_RAILS)
        for needle in ("team-guard gate review", "team-guard gate reply", "team-guard gate record",
                       "team-guard gate check", "exit 3", "exit 2"):
            with self.subTest(file="docs/guard-rails.md", needle=needle):
                self.assertIn(needle, gr)
        wf = read(WF)
        for needle in ("team-guard gate review", "team-guard gate record", "team-guard gate check"):
            with self.subTest(file="docs/workflow.md", needle=needle):
                self.assertIn(needle, wf)
        self.assertIn("team-guard gate", read(README))

        rendered = dict(("instructions/" + k, v) for k, v in instr_texts().items())
        for role in ROLES:
            rendered.update(agent_texts(role))
        rendered.update({"README.md": read(README), "docs/guard-rails.md": gr, "docs/workflow.md": wf})
        for name, doc in rendered.items():
            with self.subTest(file=name):
                self.assertNotRegex(doc, re.compile(r"no wrapper", re.I))
                self.assertNotRegex(doc, re.compile(r"\bruns? crit directly", re.I))
        for harness in ("claude-code", "codex"):
            with self.subTest(harness=harness):
                p = subprocess.run([sys.executable, str(ROOT / "build.py"), "--check", harness], cwd=ROOT,
                                   capture_output=True, text=True, timeout=120)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_instructions_say_to_background_the_review_and_re_run_it_after_a_stopped_wait(self):
        for name, doc in instr_texts().items():
            with self.subTest(file=name):
                self.has_paragraph(name, self.gate_text(doc), r"gate review", r"longest background timeout",
                                   r"re-run")


if __name__ == "__main__":
    unittest.main()
