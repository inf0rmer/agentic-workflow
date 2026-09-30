"""scripts/vendor-check: report drift between the pinned copies and an upstream ref.

It reads the pin, the copies, the stored patches and the upstream repo, and changes none of
them: moving the pin stays a person's decision. Every test here builds its own local fake
upstream, so nothing is fetched.
"""

import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from wslib import WSTestCase, tree_snapshot
from wsskills import CLONE_REASON, SOURCE, VENDOR_CHECK, insert_note

LICENSE = "MIT License\n\nCopyright (c) 2026 Test Author\n"
ALPHA = "skills/productivity/alpha"
BETA = "skills/engineering/beta"
DATE = "2026-09-29T12:00:00+00:00"


def note(path, sha, patched, d):
    tail = "modified by agentic-workflow (core/skills/patches/%s.patch)" % d if patched else "copied as-is"
    return "<!-- vendored from %s %s at %s; license: MIT (see LICENSE); %s -->" % (SOURCE, path, sha, tail)


class VendorCase(WSTestCase):

    def setUp(self):
        super().setUp()
        self.env = {"GIT_COMMITTER_DATE": DATE, "GIT_AUTHOR_DATE": DATE}
        self.U = self.sb.tmp / "upstream"
        self.U.mkdir()
        g = lambda *a: self.sb.git(self.U, *a, env=self.env)
        g("init", "-q", "-b", "main")
        files = {"LICENSE": LICENSE,
                 ALPHA + "/SKILL.md": "---\nname: alpha\ndescription: a skill\n---\n\n# Alpha\n\nline a1\n",
                 ALPHA + "/agents/openai.yaml": "interface:\n  display_name: \"Alpha\"\n",
                 BETA + "/SKILL.md": "---\nname: beta\ndescription: b skill\n---\n\n# Beta\n\nsave it to one place\nkeep this\n",
                 BETA + "/notes.md": "beta notes\n"}
        for rel, text in files.items():
            p = self.U / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        g("add", "-A")
        g("commit", "-qm", "pin")
        self.pin = g("rev-parse", "HEAD")
        self.branches = {}
        self.branch("changed", lambda: (self.U / ALPHA / "agents" / "openai.yaml").write_text("interface: {}\n"))
        self.branch("added", lambda: (self.U / ALPHA / "extra.md").write_text("extra\n"))
        self.branch("deleted", lambda: g("rm", "-q", ALPHA + "/agents/openai.yaml"))
        self.branch("removed", lambda: g("rm", "-q", "-r", BETA))
        self.branch("renamed", lambda: g("mv", ALPHA + "/agents/openai.yaml", ALPHA + "/agents/codex.yaml"))
        self.branch("conflict", lambda: (self.U / BETA / "SKILL.md").write_text(
            "---\nname: beta\ndescription: b skill\n---\n\n# Beta\n\nsave it somewhere else\nkeep this\n"))
        g("checkout", "-q", "main")
        self.F = self.sb.tmp / "fixture" / "skills"
        self.build_fixture()

    def branch(self, name, change):
        self.sb.git(self.U, "checkout", "-q", "-b", name, self.pin)
        change()
        self.sb.git(self.U, "add", "-A")
        self.sb.git(self.U, "commit", "-qm", name, env=self.env)
        self.branches[name] = self.sb.git(self.U, "rev-parse", "HEAD")

    def build_fixture(self):
        F = self.F
        F.mkdir(parents=True)
        alpha = F / "alpha"
        for f in ("SKILL.md", "agents/openai.yaml"):
            dst = alpha / f
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes((self.U / ALPHA / f).read_bytes())
        (alpha / "SKILL.md").write_text(insert_note((alpha / "SKILL.md").read_text(), note(ALPHA, self.pin, False, "alpha")))
        (alpha / "LICENSE").write_text(LICENSE)

        W = Path(tempfile.mkdtemp(prefix="patchwork-", dir=str(self.sb.tmp)))
        self.sb.git(W, "init", "-q")
        shutil.copytree(self.U / BETA, W / BETA)
        self.sb.git(W, "add", "-A")
        self.sb.git(W, "commit", "-qm", "base")
        (W / "core" / "skills").mkdir(parents=True)
        self.sb.git(W, "mv", BETA, "core/skills/beta-x")
        sk = W / "core" / "skills" / "beta-x" / "SKILL.md"
        sk.write_text(sk.read_text().replace("save it to one place", "save it in the workspace"))
        self.sb.git(W, "add", "-A")
        patch = self.sb.git(W, "diff", "--cached", "-M", "--no-color") + "\n"
        (F / "patches").mkdir()
        (F / "patches" / "beta-x.patch").write_text(patch)
        shutil.copytree(W / "core" / "skills" / "beta-x", F / "beta-x")
        (F / "beta-x" / "SKILL.md").write_text(
            insert_note((F / "beta-x" / "SKILL.md").read_text(), note(BETA, self.pin, True, "beta-x")))
        (F / "beta-x" / "LICENSE").write_text(LICENSE)
        self.upstream_json({"source": SOURCE, "sha": self.pin, "date": "2026-09-29",
                            "skills": {"alpha": {"upstream_path": ALPHA, "status": "as-is"},
                                       "beta-x": {"upstream_path": BETA, "status": "patched",
                                                  "patch": "patches/beta-x.patch"}}})

    def upstream_json(self, data):
        (self.F / "UPSTREAM.json").write_text(json.dumps(data, indent=1) if not isinstance(data, str) else data)

    def check(self, *args, ref=None, upstream=None, skills=None):
        self.assertTrue(VENDOR_CHECK.is_file(), "scripts/vendor-check exists")
        self.assertTrue(os.access(VENDOR_CHECK, os.X_OK), "scripts/vendor-check is executable")
        argv = [str(VENDOR_CHECK), "--upstream", str(upstream or self.U), "--skills-dir", str(skills or self.F)]
        if ref:
            argv += ["--ref", ref]
        return self.sb.run(argv + list(args))

    def state(self):
        return (tree_snapshot(self.F), tree_snapshot(self.U, skip=(".git",)),
                self.sb.git(self.U, "for-each-ref", "--format=%(refname) %(objectname)"),
                self.sb.git(self.U, "rev-parse", "HEAD"))


class TestReport(VendorCase):

    def test_report_names_each_kind_of_upstream_change(self):
        r = self.ok(self.check())
        self.assertEqual(r.out, "alpha: ok\nbeta-x: ok\nno drift\n")
        self.ok(self.check(ref=self.pin))
        cases = {
            "changed": "alpha: upstream changed\n  changed: agents/openai.yaml\nbeta-x: ok\n",
            "added": "alpha: upstream changed\n  added: extra.md\nbeta-x: ok\n",
            "deleted": "alpha: upstream changed\n  deleted: agents/openai.yaml\nbeta-x: ok\n",
            "renamed": "alpha: upstream changed\n  renamed: agents/openai.yaml -> agents/codex.yaml\nbeta-x: ok\n",
            "removed": "alpha: ok\nbeta-x: upstream removed\n",
            "conflict": "alpha: ok\nbeta-x: upstream changed, patch conflict\n  changed: SKILL.md\n",
        }
        for name, report in cases.items():
            with self.subTest(ref=name):
                before = self.state()
                r = self.code(self.check(ref=name), 1)
                self.assertEqual(r.out, report + "drift in 1 skill(s)\n")
                self.assertEqual(self.state(), before, "nothing is changed")

        cases = {"differs": (lambda: (self.F / "alpha" / "SKILL.md").write_text(
                     (self.F / "alpha" / "SKILL.md").read_text() + "local edit\n"), "alpha: local drift\n  differs: SKILL.md\n"),
                 "missing": (lambda: (self.F / "beta-x" / "notes.md").unlink(), "beta-x: local drift\n  missing: notes.md\n"),
                 "extra": (lambda: (self.F / "alpha" / "extra.txt").write_text("x\n"), "alpha: local drift\n  extra: extra.txt\n")}
        for name, (make, line) in cases.items():
            with self.subTest(local=name):
                shutil.rmtree(self.F.parent)
                self.build_fixture()
                make()
                r = self.code(self.check(ref=self.pin), 1)
                self.assertIn(line, r.out)
                self.assertTrue(r.out.endswith("drift in 1 skill(s)\n"), r.out)

    def test_vendor_check_refuses_when_it_cannot_run(self):
        good = json.loads((self.F / "UPSTREAM.json").read_text())
        runs = {"missing upstream": dict(upstream=self.sb.tmp / "nope"),
                "not a repo": dict(upstream=self.sb.scratch),
                "missing ref": dict(ref="no-such-ref")}
        for name, kw in runs.items():
            with self.subTest(case=name):
                r = self.code(self.check(**kw), 2)
                self.assertEqual(r.out, "")
                self.assertTrue(r.err.startswith("vendor-check: "), r.err)
        bad_json = {"missing pin": dict(good, sha="0" * 40), "invalid JSON": "{not json",
                    "extra key": dict(good, extra=1), "no skills": {k: v for k, v in good.items() if k != "skills"},
                    "bad status": dict(good, skills=dict(good["skills"], alpha={"upstream_path": ALPHA,
                                                                                "status": "vendored"}))}
        for name, data in bad_json.items():
            with self.subTest(case=name):
                self.upstream_json(data)
                before = self.state()
                r = self.code(self.check(), 2)
                self.assertEqual(r.out, "")
                self.assertTrue(r.err.startswith("vendor-check: "), r.err)
                self.assertEqual(self.state(), before)
        (self.F / "UPSTREAM.json").unlink()
        r = self.code(self.check(), 2)
        self.assertEqual(r.out, "")

    def test_vendor_check_never_changes_anything_property(self):
        rng = random.Random(3)
        refs = [None, self.pin] + sorted(self.branches)
        for i in range(8):
            ref = rng.choice(refs)
            with self.subTest(iteration=i, ref=ref):
                if rng.random() < 0.3:
                    (self.F / "alpha" / "SKILL.md").write_text("drifted %d\n" % i)
                before = self.state()
                r = self.check(ref=ref)
                self.assertIn(r.code, (0, 1), r.err)
                self.assertEqual(self.state(), before)


class TestOffline(unittest.TestCase):

    def test_skill_tests_run_offline_and_skip_without_a_local_clone(self):
        here = Path(__file__).resolve().parent
        for f in sorted(here.glob("test_workspace_skills*.py")) + [here / "test_workspace_vendor_check.py",
                                                                   here / "wsskills.py"]:
            text = f.read_text()
            with self.subTest(file=f.name):
                self.assertNotRegex(text, r"[\"'](clone|fetch|pull|ls-remote)[\"']")
                self.assertNotRegex(text, r"[\"'](pip|pip3|npm|npx)[\"']")
        env = {k: v for k, v in os.environ.items() if k != "VENDOR_UPSTREAM_CLONE"}
        p = subprocess.run([sys.executable, "-m", "unittest", "-v",
                            "test_workspace_skills.TestVendoredSet.test_every_copy_carries_the_upstream_license"],
                           cwd=str(here), env=env, capture_output=True, text=True, timeout=120)
        self.assertIn("skipped %r" % CLONE_REASON, p.stderr + p.stdout)


if __name__ == "__main__":
    unittest.main()
