"""team-guard refs: changed lines and file names must not cite workflow artifacts.

A citation of a planning document means nothing to a later reader of the code, so the
check reports added lines and changed file names that carry one, and nothing else.
"""

import json
import random
import unittest

from wstools import ToolCase, tok, realpath

CLEAN = "team-guard: refs: no workflow references in changed lines\n"
APPENDED_NOTE = tok("adden", "dum")  # the pattern name is itself a word the check reports

# (pattern name, token) pairs the built-in set must report.
MUST_MATCH = [
    ("criterion-id", tok("AC", "-12")), ("criterion-id", tok("ac", "12")), ("criterion-id", tok("AC", "_3")),
    ("criterion-id", tok("test_ac", "12_creates_file")),
    ("section-mark", tok("§", "4")), ("section-mark", tok("§ ", "4")),
    ("workflow-file", tok("contract", ".md")), ("workflow-file", tok("spec", ".md")),
    ("workflow-file", tok("architecture", ".md")), ("workflow-file", tok("brief", ".md")),
    ("workflow-file", tok("design-gate", ".md")), ("workflow-file", tok("questions", ".md")),
    ("workflow-file", tok("followups", ".md")), ("workflow-file", tok("routing", ".md")),
    (APPENDED_NOTE, tok("adden", "dum")), (APPENDED_NOTE, tok("Adden", "dum 1")),
    ("review-round", tok("round", " 2")), ("review-round", tok("round", "2")),
    ("review-round", tok("round", "-2")), ("review-round", tok("round", "_2")),
    ("adr-number", tok("ADR", " 0007")), ("adr-number", tok("ADR", "-7")),
]
MUST_NOT_MATCH = ["accuracy 5", "track1", tok("inspec", ".md"), "the round trip", "background",
                  "a contract between the two services"]
SAFE_WORDS = ["alpha", "value", "count", "track1", "accuracy 5", "background", "the round trip", "services",
              "parse", "retry", "cache", "a contract between the two services", "index", "buffer"]


def finding_lines(err):
    return [l for l in err.splitlines() if l and not l.startswith("team-guard: refs:")]


class RefsCase(ToolCase):

    def refs(self, cwd, *args, env=None):
        return self.sb.tg("refs", *args, cwd=cwd, env=env)

    def repo_with(self, files, commit=True, name="R"):
        repo = self.feature_repo(name)
        for rel, text in files.items():
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        if commit:
            self.sb.git(repo, "add", "-A")
            self.sb.git(repo, "commit", "-qm", "change")
        return repo


class TestRefsPatterns(RefsCase):

    def test_refs_reports_workflow_citations_on_added_lines_only(self):
        lines = ["package pkg"] + ["// note: %s" % t for _, t in MUST_MATCH]
        repo = self.repo_with({"pkg/refs.go": "\n".join(lines) + "\n"})
        r = self.code(self.refs(repo, "--base", "main"), 2)
        found = finding_lines(r.err)
        for i, (name, token) in enumerate(MUST_MATCH, start=2):
            with self.subTest(token=token):
                self.assertIn("pkg/refs.go:%d: %s: // note: %s" % (i, name, token), found, self.show(r))

        clean = self.repo_with({"pkg/ok.go": "package pkg\n" + "".join("// %s\n" % t for t in MUST_NOT_MATCH)},
                               name="C")
        r = self.ok(self.refs(clean, "--base", "main"))
        self.assertEqual(r.out, CLEAN)

        # A citation already on main is neither an added line nor reported when a neighbour changes,
        # and removing it is not reported either.
        base = self.sb.new_repo("B")
        self.sb.commit_file(base, "pkg/old.go", "package pkg\n// keep %s\n// drop %s\nvar x = 1\n"
                            % (tok("AC", "-7"), tok("round", " 3")), "base")
        self.sb.git(base, "checkout", "-q", "-b", "feat/x")
        self.sb.commit_file(base, "pkg/old.go", "package pkg\n// keep %s\nvar x = 2\n" % tok("AC", "-7"), "edit")
        r = self.ok(self.refs(base, "--base", "main"))
        self.assertEqual(r.out, CLEAN)

        named = self.repo_with({tok("pkg/test_ac", "12_thing.go"): "package pkg\n"}, name="N")
        r = self.code(self.refs(named, "--base", "main"), 2)
        self.assertIn(tok("pkg/test_ac", "12_thing.go: criterion-id: file name"), finding_lines(r.err), self.show(r))


class TestRefsFileSelection(RefsCase):

    def test_refs_skips_prose_and_generated_paths_and_reads_config(self):
        t = tok("AC", "-12")
        files = {"docs/guide.go": "// %s\n" % t, "notes.md": "%s\n" % t, "sub/a.rst": "%s\n" % t,
                 "b.txt": "%s\n" % t, "dist/out.js": "// %s\n" % t,
                 "pkg/code.go": "var s = %r\n" % t, "pkg/c_test.go": "// %s\n" % t,
                 "config/app.yaml": "k: %s\n" % t}
        repo = self.repo_with(files)
        (repo / "pkg" / "untracked.py").write_text("# %s\n" % t)
        (repo / "pkg" / "a.go").write_text("package pkg // %s\n" % t)  # tracked, uncommitted
        (repo / "blob.bin").write_bytes(b"\x00\x01" + t.encode() + b"\x00")
        self.sb.git(repo, "add", "blob.bin")
        r = self.code(self.refs(repo, "--base", "main"), 2)
        paths = {l.split(":", 1)[0] for l in finding_lines(r.err)}
        self.assertEqual(paths, {"pkg/code.go", "pkg/c_test.go", "config/app.yaml", "pkg/untracked.py", "pkg/a.go"},
                         self.show(r))

        # A deleted file is never scanned, whatever its name or content.
        gone = self.sb.new_repo("G")
        self.sb.commit_file(gone, tok("pkg/", "brief", ".go"), "// %s\n" % t, "base")
        self.sb.git(gone, "checkout", "-q", "-b", "feat/x")
        self.sb.git(gone, "rm", "-q", tok("pkg/", "brief", ".go"))
        self.sb.git(gone, "commit", "-qm", "delete")
        self.assertEqual(self.ok(self.refs(gone, "--base", "main")).out, CLEAN)

        cfg = self.sb.tmp / "refs.json"
        cfg.write_text(json.dumps({"patterns": [{"name": "ticket", "regex": r"TICKET-\d+"}],
                                   "disable_patterns": ["criterion-id"], "exclude": ["pkg/**"]}))
        repo2 = self.repo_with({"pkg/x.go": "// TICKET-9\n", "docs/y.go": "// ticket-4 %s\n" % t,
                                "z.go": "// %s\n" % tok("round", " 2")}, name="S")
        for how in ("flag", "env"):
            with self.subTest(how=how):
                if how == "flag":
                    bad = self.sb.tmp / "missing.json"
                    r = self.refs(repo2, "--base", "main", "--config", str(cfg), env={"TEAM_REFS_CONFIG": str(bad)})
                else:
                    r = self.refs(repo2, "--base", "main", env={"TEAM_REFS_CONFIG": str(cfg)})
                self.code(r, 2)
                self.assertEqual(finding_lines(r.err),
                                 ["docs/y.go:1: ticket: // ticket-4 %s" % t,
                                  "z.go:1: review-round: // %s" % tok("round", " 2")], self.show(r))

    def test_refs_rejects_a_bad_config_and_scans_nothing(self):
        repo = self.repo_with({"pkg/x.go": "// %s\n" % tok("AC", "-1")})
        cases = {"missing": None, "invalid-json": "{not json", "bad-regex": {"patterns": [{"name": "x", "regex": "("}]},
                 "unknown-key": {"colour": "red"}, "wrong-type": {"exclude": "pkg/**"},
                 "unknown-disable": {"disable_patterns": ["no-such-pattern"]}}
        for name, body in cases.items():
            with self.subTest(case=name):
                path = self.sb.tmp / ("cfg-%s.json" % name)
                if body is not None:
                    path.write_text(body if isinstance(body, str) else json.dumps(body))
                r = self.code(self.refs(repo, "--base", "main", "--config", str(path)), 1)
                self.has(r.err, str(path), r)
                self.assertEqual([l for l in r.err.splitlines() if l.startswith("pkg/")], [], self.show(r))
                self.lacks(r.err, "finding(s)", r)
                self.no_traceback(r)


class TestRefsOutput(RefsCase):

    def test_refs_output_is_exact_ordered_and_repeatable(self):
        repo = self.repo_with({"b.go": "// %s\n// fine\n// %s and %s\n" % (tok("round", " 2"), tok("ADR", " 0007"),
                                                                         tok("AC", "-5")),
                               "a.go": "// %s\n" % tok("adden", "dum"),
                               tok("c/test_ac", "5_x.go"): "// %s\n" % tok("§", "2")})
        r1 = self.code(self.refs(repo, "--base", "main"), 2)
        expected = [
            "a.go:1: %s: // %s" % (APPENDED_NOTE, APPENDED_NOTE),
            "b.go:1: review-round: // %s" % tok("round", " 2"),
            "b.go:3: adr-number: // %s and %s" % (tok("ADR", " 0007"), tok("AC", "-5")),
            "b.go:3: criterion-id: // %s and %s" % (tok("ADR", " 0007"), tok("AC", "-5")),
            tok("c/test_ac", "5_x.go: criterion-id: file name"),
            tok("c/test_ac", "5_x.go:1: section-mark: // ", "§", "2"),
        ]
        self.assertEqual(r1.err.splitlines(), expected + ["team-guard: refs: 6 finding(s)"], self.show(r1))
        self.assertEqual(r1.out, "")
        r2 = self.refs(repo, "--base", "main")
        self.assertEqual((r2.code, r2.out, r2.err), (r1.code, r1.out, r1.err))

        clean = self.repo_with({"ok.go": "package ok\n"}, name="C")
        r = self.ok(self.refs(clean, "--base", "main"))
        self.assertEqual((r.out, r.err), (CLEAN, ""))

        A = self.sb.new_repo("A")
        B = self.sb.new_repo("B")
        ws = self.new_ws("f")
        wtA = self.attach(A)
        wtB = self.attach(B)
        self.sb.commit_file(wtB, "x.go", "// %s\n" % tok("AC", "-1"), "b")
        self.sb.commit_file(wtA, "y.go", "// %s\n" % tok("round", "2"), "a")
        for cwd in (ws, wtB):
            with self.subTest(cwd=str(cwd)):
                r = self.code(self.refs(cwd), 2)
                self.assertEqual(r.err.splitlines(),
                                 ["A: y.go:1: review-round: // %s" % tok("round", "2"),
                                  "B: x.go:1: criterion-id: // %s" % tok("AC", "-1"),
                                  "team-guard: refs: 2 finding(s)"], self.show(r))

    def test_refs_usage_errors_exit_one(self):
        repo = self.repo_with({"ok.go": "package ok\n"})
        r = self.code(self.refs(self.sb.scratch, "--base", "main"), 1)
        self.no_traceback(r)
        r = self.code(self.refs(repo, "--base", "no-such-branch"), 1)
        self.no_traceback(r)
        r = self.code(self.refs(repo), 1)
        self.has(r.err, "--base", r)


class TestRefsProperty(RefsCase):

    def test_refs_flags_exactly_the_lines_that_carry_a_token_property(self):
        rng = random.Random(7)
        for i in range(4):
            with self.subTest(iteration=i):
                words = [" ".join(rng.choice(SAFE_WORDS) for _ in range(rng.randint(1, 4))) for _ in range(6)]
                repo = self.repo_with({"pkg/p.go": "".join("// %s\n" % w for w in words)}, name="P%d" % i)
                self.assertEqual(self.ok(self.refs(repo, "--base", "main")).out, CLEAN)
                name, token = rng.choice(MUST_MATCH)
                at = rng.randrange(len(words))
                words[at] = words[at] + " " + token
                (repo / "pkg" / "p.go").write_text("".join("// %s\n" % w for w in words))
                r = self.code(self.refs(repo, "--base", "main"), 2)
                self.assertIn("pkg/p.go:%d: %s: // %s" % (at + 1, name, words[at]), finding_lines(r.err))
                self.sb.git(repo, "checkout", "-q", "--", "pkg/p.go")
                (repo / "docs").mkdir(exist_ok=True)
                (repo / "docs" / "p.go").write_text("// %s\n" % token)
                self.assertEqual(self.ok(self.refs(repo, "--base", "main")).out, CLEAN)


if __name__ == "__main__":
    unittest.main()
