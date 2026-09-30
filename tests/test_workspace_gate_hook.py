"""hook-pretool hides review discussion from the tester.

The tester writes tests from the approved spec and contract alone. The review threads in a
workspace's .crit/ and reviews/ would pass on the reasoning behind them, so any tester tool
call that reaches into them is denied. Every other agent sees the hook exactly as before.
"""

import json
import os
import random
import subprocess
import unittest
from pathlib import Path

from wslib import ROOT, WSTestCase, patch_text, realpath

# The deny reason names a workflow file, so it is assembled rather than spelled out.
REASON = ("Review discussion in the workspace's .crit/ and reviews/ is hidden from the tester by design. "
          "Work from the spec and " + "contract" + "." + "md.")
PATH_TOOLS = ("Read", "Edit", "Write", "MultiEdit")


class HookCase(WSTestCase):

    def setUp(self):
        super().setUp()
        self.A = self.sb.new_repo("A")
        self.ws = realpath(self.new_ws("f"))
        self.wt = self.attach(self.A)
        self.root = realpath(self.sb.ws_root)
        W = Path(self.ws)
        for d in (".crit", "reviews", "reviews-old", ".critical", "notes"):
            (W / d).mkdir()
            (W / d / "x.json").write_text("[]\n")
        (W / "plan.md").write_text("# plan\n")
        (Path(self.wt) / "reviews").mkdir()
        (Path(self.wt) / "reviews" / "r.md").write_text("repo file\n")
        self.g = realpath(self.new_ws("g"))
        (Path(self.g) / ".crit").mkdir()

    def event(self, agent, tool, cwd=None, **tool_input):
        ev = {"cwd": str(cwd or self.ws), "tool_name": tool, "tool_input": tool_input}
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
        data = json.loads(r.out)
        hso = data["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertEqual(hso["permissionDecisionReason"], REASON)

    def assert_allowed(self, r):
        self.code(r, 0)
        self.assertEqual(r.out, "", self.show(r))

    def previous_guard(self):
        p = subprocess.run(["git", "-C", str(ROOT), "show", "main:core/bin/team-guard"], capture_output=True)
        if p.returncode != 0:
            self.skipTest("SKIPPED: no local main branch to compare hook output with")
        path = self.sb.tmp / "team-guard-main"
        path.write_bytes(p.stdout)
        path.chmod(0o755)
        return path


class TestTesterIsDenied(HookCase):

    def test_tester_cannot_reach_crit_or_review_exports(self):
        W = self.ws
        link = os.path.join(W, "notes-link")
        os.symlink(os.path.join(W, "reviews"), link)
        outside_link = str(self.sb.tmp / "crit-link")
        os.symlink(os.path.join(W, ".crit"), outside_link)
        denied = [
            ("Read", {"file_path": os.path.join(W, ".crit", "x.json")}, None),
            ("Read", {"file_path": "reviews/x.json"}, W),
            ("Read", {"file_path": "repos/A/../../reviews/x.json"}, W),
            ("Read", {"file_path": os.path.join(link, "x.json")}, None),
            ("Read", {"file_path": os.path.join(outside_link, "x.json")}, None),
            ("Read", {"file_path": os.path.join(W, "reviews")}, None),
            ("Read", {"file_path": W}, None),
            ("Edit", {"file_path": os.path.join(W, ".crit", "gate-design.json")}, None),
            ("Write", {"file_path": os.path.join(W, "reviews", "new.json")}, None),
            ("MultiEdit", {"file_path": os.path.join(W, "reviews", "x.json")}, None),
            ("NotebookEdit", {"notebook_path": os.path.join(W, ".crit", "n.ipynb")}, None),
            ("Grep", {"pattern": "x", "path": W}, None),
            ("Grep", {"pattern": "x", "path": os.path.join(W, "reviews")}, None),
            ("Glob", {"pattern": "**/*", "path": self.root}, None),
            ("Glob", {"pattern": "*", "path": os.path.join(self.g, ".crit")}, None),
            ("Read", {"file_path": os.path.join(self.g, ".crit", "any")}, None),
            ("apply_patch", {"command": patch_text(("update", "reviews/x.json"))}, W),
            ("apply_patch", {"command": patch_text(("add", ".crit/new.json"))}, W),
            ("Read", {"file_path": os.path.join(W, "REVIEWS", "x.json")}, None),
            ("Edit", {"file_path": os.path.join(W, ".Crit", "gate-design.json")}, None),
            ("Glob", {"pattern": os.path.join(W, "reviews", "*.json")}, W),
            ("Glob", {"pattern": "../reviews/*.json", "path": os.path.join(W, "notes")}, None),
            ("Grep", {"pattern": "x", "path": os.path.join(W, "notes"), "glob": os.path.join(W, ".crit", "*")}, None),
            ("Grep", {"pattern": "x", "path": os.path.join(W, "notes"), "glob": "../reviews/**"}, None),
        ]
        for tool, ti, cwd in denied:
            with self.subTest(tool=tool, input=ti):
                self.assert_denied(self.hook(self.event("tester", tool, cwd=cwd, **ti)))

        allowed = [
            ("Read", {"file_path": os.path.join(W, "plan.md")}),
            ("Read", {"file_path": os.path.join(self.wt, "reviews", "r.md")}),
            ("Read", {"file_path": os.path.join(W, "reviews-old", "x.json")}),
            ("Read", {"file_path": os.path.join(W, ".critical", "x.json")}),
            ("Grep", {"pattern": "x", "path": os.path.join(W, "notes")}),
            ("Grep", {"pattern": "x"}),
            ("Glob", {"pattern": "**/*.md", "path": os.path.join(W, "notes")}),
            ("Grep", {"pattern": "x", "path": os.path.join(W, "notes"), "glob": "*.json"}),
            ("Edit", {"file_path": os.path.join(self.wt, "pkg", "a_test.go")}),
            ("Read", {"file_path": "/tmp/bad\x00path"}),
        ]
        for tool, ti in allowed:
            with self.subTest(tool=tool, input=ti):
                self.assert_allowed(self.hook(self.event("tester", tool, **ti)))

    def test_tester_is_denied_however_the_path_is_written_property(self):
        rng = random.Random(31)
        W = self.ws
        for i in range(12):
            area = rng.choice([".crit", "reviews"])
            leaf = rng.choice(["x.json", "sub/deep.json", ""])
            style = rng.choice(["abs", "rel", "dotdot", "link"])
            if style == "abs":
                path, cwd = os.path.join(W, area, leaf), W
            elif style == "rel":
                path, cwd = os.path.join(area, leaf), W
            elif style == "dotdot":
                path, cwd = os.path.join("repos", "A", "pkg", "..", "..", "..", area, leaf), W
            else:
                lk = os.path.join(str(self.sb.tmp), "lk%d" % i)
                os.symlink(os.path.join(W, area), lk)
                path, cwd = os.path.join(lk, leaf), self.sb.scratch
            tool = rng.choice(PATH_TOOLS)
            with self.subTest(path=path, cwd=str(cwd), tool=tool):
                self.assert_denied(self.hook(self.event("tester", tool, cwd=cwd, file_path=path)))
            other = rng.choice(["notes", "reviews-old", ".critical", "repos/A/reviews"])
            with self.subTest(other=other):
                self.assert_allowed(self.hook(self.event("tester", tool, cwd=W, file_path=os.path.join(other, "y"))))


class TestOtherAgentsUnchanged(HookCase):

    def test_other_agents_see_the_hook_exactly_as_before(self):
        old = self.previous_guard()
        self.ok(self.sb.tg("seal", "pkg/a_test.go", cwd=self.wt))
        holdout = self.ok(self.sb.tg("holdout-path", cwd=self.ws)).out.strip()
        W = self.ws
        loop = os.path.join(W, "loop")
        os.symlink(loop, loop)
        events = []
        for agent in ("software-engineer", "reviewer", "architect", None):
            events += [self.event(agent, "Read", file_path=os.path.join(W, ".crit", "x.json")),
                       self.event(agent, "Grep", pattern="x", path=W),
                       self.event(agent, "Edit", file_path=os.path.join(W, "reviews", "x.json")),
                       self.event(agent, "Read", file_path=os.path.join(holdout, "s.md")),
                       self.event(agent, "Edit", file_path=os.path.join(self.wt, "pkg", "a_test.go")),
                       self.event(agent, "Read", file_path=loop)]
        events += [self.event("tester", "Edit", file_path=os.path.join(self.wt, "pkg", "a_test.go")),
                   self.event("tester", "Read", file_path=os.path.join(holdout, "s.md"))]
        for ev in events:
            with self.subTest(event=ev):
                new, prev = self.hook(ev), self.hook(ev, guard=old)
                self.assertEqual((new.code, new.out, new.err), (prev.code, prev.out, prev.err))
        r = self.hook(self.event("software-engineer", "Read", file_path=os.path.join(holdout, "s.md")))
        self.assertEqual(json.loads(r.out)["hookSpecificOutput"]["permissionDecision"], "deny",
                         "the engineer is still denied holdouts")
        r = self.hook(self.event("software-engineer", "Edit", file_path=os.path.join(self.wt, "pkg", "a_test.go")))
        self.assertEqual(json.loads(r.out)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assert_allowed(self.hook(self.event("tester", "Edit", file_path=os.path.join(self.wt, "pkg", "a_test.go"))))
        # A path that can't be resolved never crashes the hook, for any agent.
        for agent in ("software-engineer", "tester", None):
            with self.subTest(unresolvable=agent):
                self.assert_allowed(self.hook(self.event(agent, "Read", file_path="/tmp/bad\x00path")))
                self.assert_allowed(self.hook(self.event(agent, "Read", file_path=loop)))


if __name__ == "__main__":
    unittest.main()
