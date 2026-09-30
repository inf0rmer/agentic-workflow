"""team-guard risk: the advisory merge-risk level and the footer line for a PR.

The level comes from the checklist answers in the routing log, facts read from git and
the seal lock, and a few process flags. Unknowns count as risky so that a gap in what
the lead knows can only raise the level, never hide risk.
"""

import json
import random
import re
import unittest

from wstools import ToolCase, route_event, safe_answers, ANSWER_KEYS

RANK = {"low": 0, "medium": 1, "high": 2}
FIRST_LINE = re.compile(r"^Merge risk: (high|medium|low) \(advisory\) — .+\.$")
REASON = {
    "persisted-data": "changes stored data or its format, which a revert may not undo",
    "breaking-contract": "may break a public interface that others rely on",
    "several-repos": "spans several repositories that must ship together",
    "security": "touches security-sensitive code",
    "hard-to-revert": "a plain revert may not undo it",
    "additive-contract": "adds to a public interface that others rely on",
    "user-visible": "changes behavior users can see",
    "large-change": "is a large change",
    "config-or-ci": "changes deploy, CI or configuration files",
}
PROCESS_ONLY = "extra checks during development flagged this change for a closer look"
LOW = "docs, tests or an internal change within size limits"
BANNED = ["sealed", "unseal", "holdout", "Jev", "mutation", "AC-"]


def footer(level, reason):
    return "Merge risk: %s (advisory) — %s." % (level, reason)


def parse(out):
    lines = out.splitlines()
    rules = {}
    inputs = {}
    for l in lines[1:]:
        m = re.match(r"^rule (\S+): (high|medium|low)$", l)
        if m:
            rules[m.group(1)] = m.group(2)
        m = re.match(r"^input (\S+): (.*)$", l)
        if m:
            inputs[m.group(1)] = m.group(2)
    return (lines[0] if lines else ""), rules, inputs


class RiskCase(ToolCase):
    n = 0

    def scenario(self, files=None, answers=None, args=(), packet=True, log=False, feature=None):
        """A fresh repo with `files` committed on its branch, and a route event with `answers`."""
        RiskCase.n += 1
        feature = feature or "f%d" % RiskCase.n
        repo = self.feature_repo("R%d" % RiskCase.n)
        files = {"pkg/util.go": "package pkg\nvar U = 1\n"} if files is None else files
        for rel, text in files.items():
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        self.sb.git(repo, "add", "-A")
        self.sb.git(repo, "commit", "-qm", "change")
        if answers is not None:
            self.write_log(route_event(feature, **answers))
        argv = ["risk", "--base", "main", "--feature", feature]
        if packet:
            argv.append("--packet")
        if not log:
            argv.append("--no-log")
        return repo, feature, self.sb.tg(*argv, *args, cwd=repo)


class TestRiskRules(RiskCase):

    def test_each_rule_fires_at_its_level_from_answers_git_facts_and_flags(self):
        big = "".join("var V%d = %d\n" % (i, i) for i in range(501))
        many = {"pkg/m%02d.go" % i: "package pkg\n" for i in range(16)}
        cases = [
            ("persisted-data", "high", None, safe_answers(persisted="yes"), ()),
            ("persisted-data", "high", {"db/migrations/0001_init.sql": "create table t (x int);\n"}, safe_answers(), ()),
            ("breaking-contract", "high", None, safe_answers(), ("--contract", "breaking")),
            ("breaking-contract", "high", None, safe_answers(contract="yes"), ()),
            ("security", "high", None, safe_answers(security="yes"), ()),
            ("security", "high", {"pkg/auth/login.go": "package auth\n"}, safe_answers(), ()),
            ("hard-to-revert", "high", None, safe_answers(revertible="no"), ()),
            ("additive-contract", "medium", None, safe_answers(), ("--contract", "additive")),
            ("user-visible", "medium", None, safe_answers(user_visible="yes"), ()),
            ("large-change", "medium", many, safe_answers(), ()),
            ("large-change", "medium", {"pkg/big.go": big}, safe_answers(), ()),
            ("config-or-ci", "medium", {".github/workflows/ci.yml": "on: push\n"}, safe_answers(), ()),
            ("majors-fixed", "medium", None, safe_answers(), ("--majors-fixed", "1")),
            ("mutation-skipped", "medium", None, safe_answers(), ("--mutation-skipped", "yes")),
            ("mutation-skipped", "medium", None, safe_answers(), ("--mutation-skipped", "unknown")),
            ("holdouts-failed", "medium", None, safe_answers(), ("--holdouts-failed", "2")),
        ]
        for rule, level, files, answers, args in cases:
            with self.subTest(rule=rule, files=sorted(files or {}), args=args, answers=answers):
                _, _, r = self.scenario(files, answers, args)
                self.ok(r)
                first, rules, _ = parse(r.out)
                self.assertEqual(rules, {rule: level}, self.show(r))
                self.assertEqual(first, footer(level, REASON.get(rule, PROCESS_ONLY)), self.show(r))

        _, _, r = self.scenario(None, safe_answers())
        first, rules, inputs = parse(self.ok(r).out)
        self.assertEqual((first, rules), (footer("low", LOW), {}), self.show(r))
        self.assertEqual(inputs.get("contract"), "none")
        self.assertEqual(inputs.get("unseals"), "0")
        self.assertEqual(inputs.get("category.other"), "1")
        self.assertEqual(inputs.get("non_test_lines"), "2")
        self.assertIn("no", [v for k, v in inputs.items() if k == "persisted" or k.endswith(".persisted")],
                      "the persisted answer is listed")
        names = [l.split(":", 1)[0] for l in r.out.splitlines() if l.startswith("input ")]
        self.assertEqual(names, sorted(names), "inputs are sorted by name")
        self.assertEqual(r.out.splitlines()[1], "", "a blank line separates the footer from the breakdown")

        # An unseal in the lock history is read from the repo.
        repo = self.feature_repo("U")
        self.ok(self.sb.tg("seal", "pkg/a_test.go", cwd=repo))
        self.ok(self.sb.tg("unseal", "pkg/a_test.go", cwd=repo))
        self.sb.commit_file(repo, "pkg/util.go", "package pkg\n", "change")
        self.write_log(route_event("u", **safe_answers()))
        r = self.ok(self.sb.tg("risk", "--base", "main", "--feature", "u", "--packet", "--no-log", cwd=repo))
        first, rules, inputs = parse(r.out)
        self.assertEqual((rules, inputs.get("unseals")), ({"unseal": "medium"}, "1"), self.show(r))
        self.assertEqual(first, footer("medium", PROCESS_ONLY))

        # Several attached repos with changes.
        A = self.sb.new_repo("A")
        B = self.sb.new_repo("B")
        ws = self.new_ws("w")
        wtA = self.attach(A, "w")
        wtB = self.attach(B, "w")
        self.write_log(route_event("w", **safe_answers()))
        self.sb.commit_file(wtA, "pkg/x.go", "package pkg\n", "a")
        r = self.ok(self.sb.tg("risk", "--packet", "--no-log", cwd=ws))
        self.assertNotIn("several-repos", parse(r.out)[1], self.show(r))
        self.sb.commit_file(wtB, "pkg/y.go", "package pkg\n", "b")
        r = self.ok(self.sb.tg("risk", "--packet", "--no-log", cwd=ws))
        first, rules, inputs = parse(r.out)
        self.assertEqual(rules, {"several-repos": "high"}, self.show(r))
        self.assertEqual(first, footer("high", REASON["several-repos"]))
        self.assertEqual(inputs.get("repos_changed"), "2")

    def test_missing_answers_and_unknowns_never_lower_the_level_property(self):
        _, _, r = self.scenario(None, None, feature="never-routed")
        self.assertEqual(parse(self.ok(r).out)[0].split(" (")[0], "Merge risk: high", self.show(r))

        rng = random.Random(3)
        keys = [k for k in ANSWER_KEYS if k not in ("repos", "files")]
        for it in range(6):
            with self.subTest(iteration=it):
                base = safe_answers(**{k: rng.choice(["yes", "no"]) for k in rng.sample(keys, 2)})
                _, _, r0 = self.scenario(None, base)
                lvl0 = RANK[parse(self.ok(r0).out)[0].split()[2]]
                flip = rng.choice(keys)
                _, _, r1 = self.scenario(None, dict(base, **{flip: "unknown"}))
                self.assertGreaterEqual(RANK[parse(self.ok(r1).out)[0].split()[2]], lvl0, (flip, base))
                extra = rng.choice([("--majors-fixed", "1"), ("--holdouts-failed", "1"),
                                    ("--mutation-skipped", "yes"), ("--contract", "unknown")])
                _, _, r2 = self.scenario(None, base, extra)
                self.assertGreaterEqual(RANK[parse(self.ok(r2).out)[0].split()[2]], lvl0, (extra, base))

        _, _, r = self.scenario(None, safe_answers(), ("--majors-fixed", "3", "--holdouts-failed", "2",
                                                      "--mutation-skipped", "unknown"))
        self.assertEqual(parse(self.ok(r).out)[0], footer("medium", PROCESS_ONLY), "process facts alone never give High")

    def test_risk_refuses_a_bad_base_or_flag_with_empty_stdout(self):
        repo = self.feature_repo("E")
        for args in (("--base", "no-such-branch"), ("--base", "main", "--contract", "maybe"),
                     ("--base", "main", "--majors-fixed", "-1"), ("--base", "main", "--mutation-skipped", "perhaps"),
                     ("--base", "main", "--answer", "colour=red")):
            with self.subTest(args=args):
                r = self.code(self.sb.tg("risk", "--feature", "f", "--no-log", *args, cwd=repo), 1)
                self.assertEqual(r.out, "")
        r = self.code(self.sb.tg("risk", "--base", "main", "--no-log", cwd=self.sb.scratch), 1)
        self.assertEqual(r.out, "")
        self.assertFalse(self.log_path.exists())


class TestRiskFooter(RiskCase):

    def test_footer_line_is_exact_and_free_of_workflow_words_and_paths(self):
        _, _, r = self.scenario(None, safe_answers(persisted="yes", security="yes"))
        self.assertEqual(parse(self.ok(r).out)[0],
                         footer("high", REASON["persisted-data"] + "; " + REASON["security"]))
        _, _, r = self.scenario(None, safe_answers(persisted="yes", security="yes"), ("--contract", "breaking"))
        self.assertEqual(parse(self.ok(r).out)[0],
                         footer("high", REASON["persisted-data"] + "; " + REASON["breaking-contract"]))
        _, _, r = self.scenario(None, safe_answers(user_visible="yes"), ("--majors-fixed", "2"))
        first, rules, _ = parse(self.ok(r).out)
        self.assertEqual(first, footer("medium", REASON["user-visible"]))
        self.assertEqual(rules, {"user-visible": "medium", "majors-fixed": "medium"})

        files = {"pkg/auth/secret_handler.go": "package auth\n", "db/migrations/zz_payments.sql": "select 1;\n"}
        repo, feature, r = self.scenario(files, safe_answers(), ("--majors-fixed", "1", "--holdouts-failed", "1"))
        first = self.ok(r).out.splitlines()[0]
        self.assertRegex(first, FIRST_LINE)
        for word in BANNED + ["secret_handler", "zz_payments", "pkg/auth", "db/migrations", ".sql", ".go"]:
            with self.subTest(word=word):
                self.assertNotIn(word.lower(), first.lower())
        r2 = self.sb.tg("risk", "--base", "main", "--feature", feature, "--packet", "--no-log",
                        "--majors-fixed", "1", "--holdouts-failed", "1", cwd=repo)
        self.assertEqual((r2.code, r2.out), (r.code, r.out), "the same inputs give the same output")

        _, _, r = self.scenario(None, safe_answers(), packet=False)
        self.assertEqual(self.ok(r).out, footer("low", LOW) + "\n", "without --packet only the footer is printed")


class TestRiskLog(RiskCase):

    def test_risk_logs_only_enums_and_counts(self):
        files = {"pkg/core_test.go": "package pkg\n", "tests/migrations/0001.sql": "select 1;\n",
                 "db/migrations/0002.sql": "select 2;\n", "src/oauth/token.go": "package oauth\n",
                 "deploy/app.yaml": "k: v\n", "docs/guide_zq.md": "# guide\n", "pkg/core_zq.go": "package pkg\n"}
        repo, feature, r = self.scenario(files, safe_answers(), ("--human-level", "medium", "--majors-fixed", "1"),
                                         packet=False, log=True)
        self.ok(r)
        risk = [e for e in self.log_lines() if e.get("event") == "risk"]
        self.assertEqual(len(risk), 1, self.log_path.read_text())
        e = risk[0]
        self.assertEqual((e["v"], e["feature"], e["level"], e["human_level"]), (1, feature, "high", "medium"))
        self.assertIn("persisted-data", e["rules"])
        self.assertIn("security", e["rules"])
        self.assertIn("majors-fixed", e["rules"])
        inputs = e["inputs"]
        self.assertEqual(inputs["categories"], {"test": 2, "migration_or_schema": 1, "auth_or_security": 1,
                                                "config_or_ci": 1, "docs": 1, "other": 1})
        self.assertEqual((inputs["files"], inputs["majors_fixed"], inputs["unseals"]), (7, 1, 0))

        def leaves(x):
            if isinstance(x, dict):
                for v in x.values():
                    yield from leaves(v)
            elif isinstance(x, list):
                for v in x:
                    yield from leaves(v)
            else:
                yield x

        enum = {"yes", "no", "unknown", "none", "additive", "breaking", "<=3", "4-15", ">15"}
        for v in leaves(inputs):
            with self.subTest(value=v):
                self.assertTrue(isinstance(v, int) or v in enum, v)
        text = json.dumps(e)
        for frag in ("core_test", "0001", "0002", "oauth/", "token", "deploy/", "guide_zq", "core_zq", "app.yaml"):
            self.assertNotIn(frag, text)
        self.assertEqual(sum(1 for x in self.log_lines() if x.get("event") == "risk"), 1)
        self.sb.tg("risk", "--base", "main", "--feature", feature, "--no-log", cwd=repo)
        self.assertEqual(sum(1 for x in self.log_lines() if x.get("event") == "risk"), 1, "--no-log writes nothing")


if __name__ == "__main__":
    unittest.main()
