"""The vendored skills under core/skills: exactly the pinned subset, traceable to upstream.

Each copy either matches upstream byte for byte or is upstream plus a stored patch, carries
its license and a provenance line, and never tells an agent to write outside the workspace.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from wslib import ROOT
from wsskills import (ALL_DIRS, FILES, PIN, SK, SKILLS, SOURCE, SOURCE_OF, SOURCES, TOB_PIN, clone_or_skip, files_under, frontmatter_value,
                      git_bytes, insert_note, note_for, split_frontmatter, strip_note, upstream_file, upstream_files)

FORBIDDEN = [r"docs/adr", r"\$TMPDIR", r"\.scratch", r"temporary directory", r"/tmp\b", r"repo(sitory)? root",
             r"root of the repo"]


def read(d, f="SKILL.md"):
    return (SK / d / f).read_text(encoding="utf-8")


class TestVendoredSet(unittest.TestCase):

    def test_exactly_the_pinned_skills_are_vendored_with_their_files(self):
        dirs = sorted(p.name for p in SK.iterdir() if p.is_dir() and p.name != "patches")
        self.assertEqual(dirs, ALL_DIRS)
        self.assertTrue((SK / "UPSTREAM.json").is_file())
        self.assertTrue((SK / "patches").is_dir())
        for d in ALL_DIRS:
            with self.subTest(skill=d):
                self.assertEqual(frontmatter_value(read(d), "name"), d)
        for d, want in FILES.items():
            with self.subTest(files=d):
                self.assertEqual(files_under(SK / d), want)

    def test_as_is_skills_match_upstream_bytes(self):
        clone = clone_or_skip(self)
        for d, (path, status) in SKILLS.items():
            if status != "as-is":
                continue
            with self.subTest(skill=d):
                ups = upstream_files(clone, path)
                self.assertEqual(files_under(SK / d), set(ups) | {"LICENSE"})
                for f in ups:
                    got = (SK / d / f).read_bytes()
                    if f == "SKILL.md":
                        got = strip_note(got.decode("utf-8")).encode("utf-8")
                    self.assertEqual(got, upstream_file(clone, path, f), f)
        p = subprocess.run([str(ROOT / "scripts" / "vendor-check"), "--source", "mattpocock", "--upstream", str(clone), "--ref", PIN],
                           capture_output=True, text=True, timeout=120) if (ROOT / "scripts" / "vendor-check").exists() \
            else None
        self.assertIsNotNone(p, "scripts/vendor-check exists")
        for d in ("grilling", "writing-for-agents"):
            self.assertIn("%s: ok" % d, p.stdout.splitlines(), p.stdout + p.stderr)

    def test_every_copy_carries_an_exact_provenance_line(self):
        sources = json.loads((SK / "UPSTREAM.json").read_text())["sources"]
        for d, (_, status) in SKILLS.items():
            with self.subTest(skill=d):
                fm, rest = split_frontmatter(read(d))
                self.assertIsNotNone(fm)
                self.assertEqual(rest[0], note_for(d, sources[SOURCE_OF[d]]["sha"]))
                self.assertIn("license: %s (see LICENSE)" % SOURCES[SOURCE_OF[d]]["license"], rest[0])
                self.assertEqual("modified" in rest[0], status == "patched")
                self.assertEqual(sum(1 for l in rest if l.startswith("<!-- vendored from ")), 1)

    def test_every_copy_carries_the_upstream_license(self):
        clone = clone_or_skip(self)
        lic = git_bytes(clone, "show", "%s:LICENSE" % PIN)
        self.assertIn(b"MIT License", lic)
        self.assertIn(b"Matt Pocock", lic)
        for d in SKILLS:
            if SOURCE_OF[d] == "mattpocock":
                with self.subTest(skill=d):
                    self.assertEqual((SK / d / "LICENSE").read_bytes(), lic)

    def test_trail_of_bits_copies_carry_the_share_alike_license(self):
        clone = clone_or_skip(self, "trailofbits")
        lic = git_bytes(clone, "show", "%s:LICENSE" % TOB_PIN)
        self.assertIn(b"Attribution-ShareAlike 4.0", lic)
        for d in ("differential-review", "fp-check"):
            with self.subTest(skill=d):
                self.assertEqual((SK / d / "LICENSE").read_bytes(), lic)

    def test_upstream_record_names_the_pin_and_every_patch(self):
        data = json.loads((SK / "UPSTREAM.json").read_text())
        self.assertEqual(set(data), {"sources", "skills"})
        self.assertEqual(set(data["sources"]), set(SOURCES))
        tob = data["sources"]["trailofbits"]
        self.assertEqual(tob, {k: SOURCES["trailofbits"][k] for k in ("url", "sha", "date", "license")})
        src = data["sources"]["mattpocock"]
        self.assertEqual(set(src), {"url", "sha", "date", "license"})
        self.assertEqual((src["url"], src["sha"], src["date"], src["license"]), (SOURCE, PIN, "2026-09-29", "MIT"))
        self.assertRegex(src["sha"], r"^[0-9a-f]{40}$")
        self.assertEqual(set(data["skills"]), set(SKILLS))
        named = set()
        for d, entry in data["skills"].items():
            with self.subTest(skill=d):
                path, status = SKILLS[d]
                self.assertEqual(entry["source"], SOURCE_OF[d])
                self.assertEqual(entry["upstream_path"], path)
                self.assertEqual(entry["status"], status)
                if status == "patched":
                    self.assertEqual(set(entry), {"source", "upstream_path", "status", "patch"})
                    self.assertEqual(entry["patch"], "patches/%s.patch" % d)
                    self.assertTrue((SK / entry["patch"]).is_file())
                    named.add(entry["patch"])
                else:
                    self.assertEqual(set(entry), {"source", "upstream_path", "status"})
        self.assertEqual({"patches/" + p.name for p in (SK / "patches").iterdir()}, named)

    def test_each_patched_copy_is_upstream_plus_its_stored_patch(self):
        self.check_patched_copies(clone_or_skip(self), "mattpocock", PIN)

    def test_each_patched_trail_of_bits_copy_is_upstream_plus_its_stored_patch(self):
        self.check_patched_copies(clone_or_skip(self, "trailofbits"), "trailofbits", TOB_PIN)

    def check_patched_copies(self, clone, source, pin):
        lic = git_bytes(clone, "show", "%s:LICENSE" % pin)
        for d, (path, status) in SKILLS.items():
            if status != "patched" or SOURCE_OF[d] != source:
                continue
            with self.subTest(skill=d), tempfile.TemporaryDirectory() as t:
                T = Path(t)
                for f in upstream_files(clone, path, pin):
                    dst = T / path / f
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_bytes(upstream_file(clone, path, f, pin))
                patch = (SK / "patches" / ("%s.patch" % d)).resolve()
                p = subprocess.run(["git", "apply", str(patch)], cwd=t, capture_output=True, text=True,
                                   env=dict(os.environ, GIT_CONFIG_NOSYSTEM="1"))
                self.assertEqual(p.returncode, 0, p.stderr)
                out = T / "core" / "skills" / d
                skill = out / "SKILL.md"
                skill.write_text(insert_note(skill.read_text(encoding="utf-8"), note_for(d, pin)), encoding="utf-8")
                (out / "LICENSE").write_bytes(lic)
                self.assertEqual(files_under(out), files_under(SK / d))
                for f in files_under(out):
                    self.assertEqual((out / f).read_bytes(), (SK / d / f).read_bytes(), f)
                self.assertFalse((T / path).exists() and any((T / path).rglob("*")), "every upstream file moved")


class TestPatchedContent(unittest.TestCase):

    def test_skills_write_only_under_the_workspace(self):
        for d in SKILLS:
            for f in sorted(files_under(SK / d)):
                text = (SK / d / f).read_text(encoding="utf-8", errors="replace")
                for pat in FORBIDDEN:
                    with self.subTest(file="%s/%s" % (d, f), pattern=pat):
                        self.assertNotRegex(text, re.compile(pat, re.I))
        need = {"handoff": ["handoff-<slug>.md"], "diagnosing-bugs": []}
        for d, paths in need.items():
            text = "\n".join((SK / d / f).read_text(encoding="utf-8") for f in sorted(files_under(SK / d))
                             if f != "LICENSE")
            with self.subTest(skill=d):
                self.assertIn("team-guard workspace-path", text)
                for p in paths:
                    self.assertIn(p, text)

    def test_no_skill_mentions_the_removed_roles_or_locks(self):
        for d in SKILLS:
            for f in sorted(files_under(SK / d)):
                text = (SK / d / f).read_text(encoding="utf-8", errors="replace")
                for word in ("sealed", "tester", "holdout", "codebase-design", "domain-modeling"):
                    with self.subTest(file="%s/%s" % (d, f), word=word):
                        self.assertNotIn(word, text)

    def test_trail_of_bits_skills_are_loaded_by_name_and_cannot_edit_or_delegate(self):
        for d in ("differential-review", "fp-check"):
            text = read(d)
            with self.subTest(skill=d):
                self.assertIn("Loaded by name from the security-reviewer role only", frontmatter_value(text, "description"))
                self.assertEqual(frontmatter_value(text, "allowed-tools"), "Read Grep Glob Bash")
                self.assertIn("\npolicy:\n  allow_implicit_invocation: false",
                              "\n" + read(d, "agents/openai.yaml"))

    def test_trail_of_bits_skills_return_the_report_and_never_name_removed_machinery(self):
        for d in ("differential-review", "fp-check"):
            for f in sorted(files_under(SK / d)):
                if f == "LICENSE" or f.endswith(".svg"):
                    continue
                text = read(d, f)
                for word in ("adversarial-modeler", "issue-writer", "audit-context-building", "{baseDir}",
                             "AskUserQuestion", "TaskCreate", "poc-builder", "data-flow-analyzer",
                             "exploitability-verifier", "DIFFERENTIAL_REVIEW", "domain-specific-audits",
                             "checkout", "worktree"):
                    with self.subTest(file="%s/%s" % (d, f), word=word):
                        self.assertNotIn(word, text)
                with self.subTest(file="%s/%s" % (d, f), word="gh"):
                    self.assertIsNone(re.search(r"\bgh\b", text))
        self.assertIn("final message", read("differential-review", "reporting.md"))

    def test_bug_fix_section_gives_the_whole_loop_to_the_engineer(self):
        _, rest = split_frontmatter(read("diagnosing-bugs"))
        body = [l for l in rest[1:]]
        first = next(l for l in body if l.strip())
        self.assertEqual(first, "## In a Quick task")
        start = body.index(first)
        end = next((i for i in range(start + 1, len(body)) if body[i].startswith("## ")), len(body))
        sec = "\n".join(body[start:end])
        for word in ("engineer", "feedback loop", "reproduce", "regression test", "before the fix", "fails on the defect"):
            with self.subTest(word=word):
                self.assertIn(word.lower(), sec.lower())

    def test_tdd_skill_is_upstream_tdd_bound_to_the_design_doc(self):
        text = read("tdd")
        self.assertEqual(frontmatter_value(text, "name"), "tdd")
        desc = frontmatter_value(text, "description") or ""
        for w in ("software-engineer", "by name"):
            self.assertIn(w, desc)
        for w in ("test-first", "red-green-refactor", "integration tests", "Use when"):
            self.assertNotIn(w, desc)
        self.assertNotIn("confirm them with the user", text)
        self.assertNotIn("What's the public interface, and which seams should we test?", text)
        _, rest = split_frontmatter(text)
        start = rest.index("## Seams: where tests go")
        end = next((i for i in range(start + 1, len(rest)) if rest[i].startswith("## ")), len(rest))
        seams = "\n".join(rest[start:end])
        for w in ("How we'll know it works", "tests.md"):
            self.assertIn(w, seams)
        self.assertTrue(any("bend an existing test" in l for l in text.splitlines()), "no bending existing tests")

    def test_tdd_skill_differs_from_upstream_only_where_the_workflow_needs(self):
        _, rest = split_frontmatter(read("tdd"))
        clone = clone_or_skip(self)
        up = upstream_file(clone, "skills/engineering/tdd", "agents/openai.yaml").decode()
        mine = (SK / "tdd" / "agents" / "openai.yaml").read_text()
        self.assertIn("\npolicy:\n  allow_implicit_invocation: false", "\n" + mine)
        extra = {"policy:", "  allow_implicit_invocation: false", ""}
        self.assertEqual([l for l in mine.splitlines() if l not in extra], [l for l in up.splitlines() if l != ""])
        for f in ("tests.md", "mocking.md"):
            self.assertEqual((SK / "tdd" / f).read_bytes(), upstream_file(clone, "skills/engineering/tdd", f))
            patch = (SK / "patches" / "tdd.patch").read_text()
            self.assertIn("similarity index 100%%\nrename from skills/engineering/tdd/%s\n"
                          "rename to core/skills/tdd/%s" % (f, f), patch)

        up_md = upstream_file(clone, "skills/engineering/tdd", "SKILL.md").decode()
        _, up_body = split_frontmatter(up_md)
        keep, in_seams, first_para_done, seen_text = [], False, False, False
        for l in up_body:
            if l.startswith("## "):
                in_seams = l == "## Seams: where tests go"
                first_para_done, seen_text = False, False
                keep.append(l)
                continue
            if in_seams:
                if first_para_done:
                    continue
                if l.strip():
                    seen_text = True
                elif seen_text:
                    first_para_done = True
            keep.append(l)
        mine_lines = rest[1:]
        it = iter(mine_lines)
        missing = [l for l in keep if not any(l == m for m in it)]
        self.assertEqual(missing, [], "every other upstream line is kept, in order")


class TestBuiltSkills(unittest.TestCase):

    def test_both_harnesses_ship_exactly_the_vendored_skills(self):
        for harness in ("claude-code", "codex"):
            out = ROOT / "dist" / harness / "skills"
            with self.subTest(harness=harness):
                self.assertEqual(sorted(p.name for p in out.iterdir()), ALL_DIRS)
                for d in ALL_DIRS:
                    self.assertEqual(files_under(out / d), files_under(SK / d), d)
                    for f in files_under(SK / d):
                        if f.endswith("SKILL.md"):
                            self.assertNotIn("{{", (out / d / f).read_text(encoding="utf-8"))
                        else:
                            self.assertEqual((out / d / f).read_bytes(), (SK / d / f).read_bytes(), "%s/%s" % (d, f))
                p = subprocess.run([sys.executable, str(ROOT / "build.py"), "--check", harness], cwd=ROOT,
                                   capture_output=True, text=True, timeout=120)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_skill_calls_render_per_harness_from_adapter_placeholders(self):
        # patches/ and UPSTREAM.json record upstream (a patch must quote the lines it removes), so only
        # the vendored skill files are held to the rule.
        for f in sorted(SK.rglob("*")):
            if f.is_file() and f.relative_to(SK).parts[0] not in ("patches", "UPSTREAM.json"):
                with self.subTest(file=str(f.relative_to(ROOT))):
                    self.assertNotIn(b"Skill tool", f.read_bytes())
        want = {"claude-code": {"skill_call": "call the Skill tool with", "skill_sigil": ""},
                "codex": {"skill_call": "mention", "skill_sigil": "$"}}
        for harness, vars_ in want.items():
            adapter = json.loads((ROOT / "adapters" / harness / "adapter.json").read_text())
            for k, v in vars_.items():
                with self.subTest(harness=harness, var=k):
                    self.assertEqual(adapter["vars"].get(k), v)
        callers = [d for d in SKILLS if "{{skill_call}}" in read(d)]
        self.assertIn("handoff", callers)
        for d in callers:
            cc = (ROOT / "dist" / "claude-code" / "skills" / d / "SKILL.md").read_text(encoding="utf-8")
            cx = (ROOT / "dist" / "codex" / "skills" / d / "SKILL.md").read_text(encoding="utf-8")
            with self.subTest(skill=d):
                self.assertIn("Skill tool", cc)
                self.assertNotIn("Skill tool", cx)
                self.assertRegex(cx, r"`\$[a-z][a-z0-9-]*`")

        with tempfile.TemporaryDirectory() as t:
            copy = Path(t) / "repo"
            shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            probe = copy / "core" / "skills" / "zz-probe"
            probe.mkdir()
            (probe / "SKILL.md").write_text("---\nname: zz-probe\ndescription: probe\n---\n{{no_such_var}}\n")
            p = subprocess.run([sys.executable, str(copy / "build.py"), "claude-code"], cwd=copy, capture_output=True,
                               text=True, timeout=120)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("uses {{no_such_var}}, which the adapter doesn't define", p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
