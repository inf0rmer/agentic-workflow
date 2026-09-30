"""hook-pretool keeps the engineer's inner-loop skill away from the tester.

The tester writes the sealed tests from the spec alone; loading the engineer's test-first
loop would pull it toward the implementation. So a tester's Skill call for tdd-inner-loop
is denied, and every other call and every existing rule behaves as before.
"""

import json
import random
import re
import subprocess
import unittest

from wslib import ROOT, WSTestCase

# The reason names a workflow file, so it is assembled rather than spelled out.
REASON = ("tdd-inner-loop is the software-engineer's inner loop, not the tester's. Work from the spec and "
          + "contract" + "." + "md.")
TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit", "Read", "Grep", "Glob")


def normalize(s):
    s = s.strip()
    if s.startswith("/"):
        s = s[1:]
    return s.rsplit(":", 1)[-1]


class SkillHookCase(WSTestCase):

    def skill_event(self, agent, skill=None, has_skill=True):
        ti = {"args": "x"}
        if has_skill:
            ti["skill"] = skill
        ev = {"tool_name": "Skill", "tool_input": ti, "cwd": str(self.sb.scratch)}
        if agent is not None:
            ev["agent_type"] = agent
        return json.dumps(ev)

    def hook(self, ev, guard=None):
        if guard is None:
            return self.sb.tg("hook-pretool", input=ev)
        return self.sb.run([str(guard), "hook-pretool"], input=ev)

    def assert_denied(self, r):
        self.code(r, 0)
        if not r.out.strip():
            self.fail("expected a deny decision, got none\n%s" % self.show(r))
        hso = json.loads(r.out)["hookSpecificOutput"]
        self.assertEqual((hso["permissionDecision"], hso["permissionDecisionReason"]), ("deny", REASON))

    def assert_silent(self, r):
        self.code(r, 0)
        self.assertEqual(r.out, "", self.show(r))


class TestSkillDenial(SkillHookCase):

    def test_tester_cannot_load_the_inner_loop_skill(self):
        for s in ("tdd-inner-loop", " /tdd-inner-loop ", "plugin:tdd-inner-loop", "/plugin:tdd-inner-loop",
                  "a:b:tdd-inner-loop"):
            with self.subTest(skill=s):
                self.assert_denied(self.hook(self.skill_event("tester", s)))

    def test_other_skill_calls_and_agents_get_no_decision(self):
        for s in ("tdd", "tdd-inner-loop-x", "diagnosing-bugs", "TDD-INNER-LOOP", "tdd-inner-loop:x", ""):
            with self.subTest(tester_skill=s):
                self.assert_silent(self.hook(self.skill_event("tester", s)))
        for bad in (5, None, ["tdd-inner-loop"], {"name": "tdd-inner-loop"}):
            with self.subTest(value=bad):
                self.assert_silent(self.hook(self.skill_event("tester", bad)))
        self.assert_silent(self.hook(self.skill_event("tester", has_skill=False)))
        for agent in ("software-engineer", "reviewer", "architect", None):
            with self.subTest(agent=agent):
                self.assert_silent(self.hook(self.skill_event(agent, "tdd-inner-loop")))

        p = subprocess.run(["git", "-C", str(ROOT), "show", "main:core/bin/team-guard"], capture_output=True)
        if p.returncode != 0:
            self.skipTest("SKIPPED: no local main branch to compare hook output with")
        old = self.sb.tmp / "team-guard-main"
        old.write_bytes(p.stdout)
        old.chmod(0o755)
        A = self.sb.new_repo("A")
        self.new_ws("f")
        wt = self.attach(A)
        self.ok(self.sb.tg("seal", "pkg/a_test.go", cwd=wt))
        holdout = self.ok(self.sb.tg("holdout-path", cwd=wt)).out.strip()
        for agent in ("software-engineer", "tester", None):
            for tool, path in (("Edit", "%s/pkg/a_test.go" % wt), ("Read", "%s/s.md" % holdout),
                               ("Write", "%s/pkg/a.go" % wt)):
                ev = {"cwd": str(wt), "tool_name": tool, "tool_input": {"file_path": path}}
                if agent:
                    ev["agent_type"] = agent
                ev = json.dumps(ev)
                with self.subTest(agent=agent, tool=tool):
                    new, prev = self.hook(ev), self.hook(ev, guard=old)
                    self.assertEqual((new.code, new.out, new.err), (prev.code, prev.out, prev.err))

    def test_tester_is_denied_exactly_when_the_name_normalizes_to_the_inner_loop_property(self):
        rng = random.Random(41)
        parts = ["tdd-inner-loop", "tdd", "inner-loop", "x", ":", "/", " ", "p:", "tdd-inner-loop-", "-"]
        for i in range(30):
            s = "".join(rng.choice(parts) for _ in range(rng.randint(1, 3)))
            with self.subTest(skill=repr(s)):
                r = self.hook(self.skill_event("tester", s))
                if normalize(s) == "tdd-inner-loop":
                    self.assert_denied(r)
                else:
                    self.assert_silent(r)


class TestSkillMatcher(unittest.TestCase):

    def test_claude_code_hooks_the_skill_tool_and_docs_say_where_the_rule_holds(self):
        data = json.loads((ROOT / "adapters" / "claude-code" / "settings.hooks.json").read_text())
        matchers = [m.get("matcher", "") for m in data["hooks"]["PreToolUse"]
                    if "hook-pretool" in json.dumps(m.get("hooks", []))]
        self.assertTrue(matchers, "a PreToolUse entry runs hook-pretool")
        for tool in ("Skill",) + TOOLS:
            with self.subTest(tool=tool):
                self.assertTrue(any(re.fullmatch(m, tool) for m in matchers), (tool, matchers))
        p = subprocess.run(["git", "-C", str(ROOT), "show", "main:adapters/codex/hooks.json"], capture_output=True)
        if p.returncode == 0:
            self.assertEqual((ROOT / "adapters" / "codex" / "hooks.json").read_bytes(), p.stdout,
                             "the Codex hooks are unchanged")
        for doc in ("README.md", "docs/guard-rails.md"):
            text = (ROOT / doc).read_text()
            with self.subTest(doc=doc):
                self.assertTrue(any("tdd-inner-loop" in l and "Claude Code" in l and "identifiable" in l
                                    for l in text.splitlines()), "a line says where the Skill rule is enforced")
                self.assertIn("not enforced on Codex", text)


if __name__ == "__main__":
    unittest.main()
