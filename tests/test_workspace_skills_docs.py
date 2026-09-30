"""The rendered roles and instructions name each vendored skill where it is used.

An agent only uses a skill its instructions name, and the tester must never be pointed at
the engineer's inner loop. The docs credit the upstream author and say the copies are
pinned, so nobody expects them to update themselves.
"""

import re
import unittest

from test_workflow_rules import H_STEPS, ROLES, ROOT, README, agent_texts, instr_texts, read, section
from wsskills import SKILLS


def paragraphs(text):
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


class TestSkillWiring(unittest.TestCase):
    maxDiff = None

    def has_paragraph(self, name, text, *patterns):
        hits = [p for p in paragraphs(text) if all(re.search(x, p, re.I) for x in patterns)]
        self.assertTrue(hits, "%s: no paragraph matches all of %r" % (name, patterns))

    def test_roles_and_instructions_name_each_skill_where_it_is_used(self):
        for name, doc in agent_texts("product-owner").items():
            self.has_paragraph(name, doc, r"grilling", r"question")
        for name, doc in instr_texts().items():
            with self.subTest(file=name):
                steps = section(doc, H_STEPS)
                self.assertIsNotNone(steps)
                self.assertIn("grilling", steps)
                self.assertIn("diagnosing-bugs", doc)
                sigil = "/" if name == "claude-code" else "$"
                self.has_paragraph(name, doc, re.escape(sigil + "handoff"), r"to-questionnaire", r"human")
                self.assertLessEqual(len(doc.encode("utf-8")), 32768)
        for name, doc in agent_texts("architect").items():
            with self.subTest(file=name):
                for w in ("codebase-design", "domain-modeling", "adr/"):
                    self.assertIn(w, doc)
        for name, doc in agent_texts("tester").items():
            with self.subTest(file=name):
                self.has_paragraph(name, doc, r"diagnosing-bugs", r"feedback loop", r"regression test")
                self.assertTrue(any("inner-loop skill" in l and "never" in l for l in doc.splitlines()),
                                "the tester is told it never loads the inner-loop skill")
        for name, doc in agent_texts("software-engineer").items():
            with self.subTest(file=name):
                self.has_paragraph(name, doc, r"diagnosing-bugs", r"hypothes", r"\bfix")
                call = "call the Skill tool with `tdd-inner-loop`" if name.startswith("claude-code") \
                    else "mention `$tdd-inner-loop`"
                self.assertIn(call, doc)

    def test_only_the_engineer_role_names_the_inner_loop_skill(self):
        for role in ROLES:
            for name, doc in agent_texts(role).items():
                with self.subTest(file=name):
                    self.assertEqual("tdd-inner-loop" in doc, role == "software-engineer")

    def test_docs_list_and_credit_the_pinned_skills(self):
        docs = {"README.md": read(README), "docs/harnesses.md": read(ROOT / "docs" / "harnesses.md"),
                "docs/guard-rails.md": read(ROOT / "docs" / "guard-rails.md")}
        docs.update({"instructions/" + k: v for k, v in instr_texts().items()})
        for name, doc in docs.items():
            with self.subTest(file=name):
                for d in SKILLS:
                    self.assertIn(d, doc)
                for w in ("Matt Pocock", "MIT", "pinned", "never auto-update", "plugin is not installed",
                          "scripts/vendor-check"):
                    self.assertIn(w, doc)
        everything = dict(docs)
        for role in ROLES:
            everything.update(agent_texts(role))
        everything["docs/workflow.md"] = read(ROOT / "docs" / "workflow.md")
        for name, doc in everything.items():
            for line in doc.splitlines():
                if re.search(r"grilling|vendor-check", line, re.I):
                    with self.subTest(file=name, line=line.strip()[:100]):
                        self.assertNotRegex(line, re.compile(r"planned", re.I))


if __name__ == "__main__":
    unittest.main()
