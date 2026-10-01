"""team-guard risk: the review budget, its warning, and how change size feeds the risk level.

A reviewer can only judge a change of bounded size. `risk` counts the lines a human has
to read (added plus removed, outside test and generated paths), compares them with a
budget, warns when the change is over it and treats an over-budget change as Medium
risk. The PR footer names the level and the scope reason only: it never mentions size.
"""

import random
import re
import unittest

from wstools import ToolCase, route_event, safe_answers

BUDGET_ENV = "TEAM_PR_BUDGET"
WARN_PREFIX = "Review-size warning:"
FIRST_LINE = re.compile(r"^Merge risk: (high|medium|low) \(advisory\)( — .+)?\.$")
LOW = "Merge risk: low (advisory) — docs, tests or an internal change."
PROCESS_ONLY = "extra checks during development flagged this change for a closer look"
USER_VISIBLE = "changes behavior users can see"
PERSISTED = "changes stored data or its format, which a revert may not undo"
GENERATED = ("dist/app/bundle.js", "pkg/api_gen.go", "web/client.gen.ts", "pkg/zz_generated.deepcopy.go",
             "pkg/api.pb.go", "py/api_pb2.py")


def warning(n, budget):
    return ("Review-size warning: %d changed lines to review exceeds the review budget of %d; "
            "split before asking for review." % (n, budget))


def lines(n, prefix="v"):
    return "".join("%s%d\n" % (prefix, i) for i in range(n))


def parse(out):
    all_lines = out.splitlines()
    rules, inputs = {}, {}
    for l in all_lines[1:]:
        m = re.match(r"^rule (\S+): (high|medium|low)$", l)
        if m:
            rules[m.group(1)] = m.group(2)
        m = re.match(r"^input (\S+): (.*)$", l)
        if m:
            inputs[m.group(1)] = m.group(2)
    return (all_lines[0] if all_lines else ""), rules, inputs


def warnings(out):
    return [l for l in out.splitlines() if l.startswith(WARN_PREFIX)]


class BudgetCase(ToolCase):
    n = 0

    def change(self, files, answers=None, feature=None):
        """A fresh repo whose branch adds `files` over main, and a route event for the feature."""
        BudgetCase.n += 1
        feature = feature or "b%d" % BudgetCase.n
        repo = self.feature_repo("B%d" % BudgetCase.n)
        for rel, text in files.items():
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        self.sb.git(repo, "add", "-A")
        self.sb.git(repo, "commit", "-qm", "change")
        self.write_log(route_event(feature, **(answers or safe_answers())))
        return repo, feature

    def risk(self, repo, feature, *args, budget=None, env_budget=None, packet=True, log=False):
        """Run risk; `budget` goes to the flag, `env_budget` to the variable (None leaves it unset)."""
        argv = ["risk", "--base", "main", "--feature", feature]
        if packet:
            argv.append("--packet")
        if not log:
            argv.append("--no-log")
        if budget is not None:
            argv += ["--pr-budget", str(budget)]
        argv += list(args)
        if env_budget is None:
            return self.sb.tg(*argv, cwd=repo, unset=(BUDGET_ENV,))
        return self.sb.tg(*argv, cwd=repo, env={BUDGET_ENV: env_budget})

    def risk_events(self):
        return [e for e in self.log_lines() if e.get("event") == "risk"]


class TestReviewSizeWarning(BudgetCase):

    def test_over_budget_change_ends_output_with_a_warning_naming_count_and_budget(self):
        # Docs count as lines to review, alongside code.
        repo, feature = self.change({"pkg/big.go": lines(30), "docs/guide.md": lines(7)})
        r = self.ok(self.risk(repo, feature, budget=36))
        self.assertEqual(r.out.splitlines()[-1], warning(37, 36), self.show(r))
        self.assertEqual(warnings(r.out), [warning(37, 36)], self.show(r))
        r = self.ok(self.risk(repo, feature, budget=36, packet=False))
        self.assertEqual(r.out.splitlines()[-1], warning(37, 36), self.show(r))
        self.assertRegex(r.out.splitlines()[0], FIRST_LINE, self.show(r))

    def test_change_at_or_under_budget_or_large_only_in_tests_and_generated_code_has_no_warning(self):
        repo, feature = self.change({"pkg/big.go": lines(37)})
        for budget in (37, 38):
            with self.subTest(budget=budget):
                r = self.ok(self.risk(repo, feature, budget=budget))
                self.assertEqual(warnings(r.out), [], self.show(r))
                self.assertNotIn("large-change", parse(r.out)[1], self.show(r))

        heavy = {"pkg/one.go": "x\n", "tests/big_test.py": lines(5000)}
        for rel in GENERATED:
            heavy[rel] = lines(5000)
        repo, feature = self.change(heavy)
        r = self.ok(self.risk(repo, feature, budget=1))
        self.assertEqual(warnings(r.out), [], self.show(r))
        self.assertNotIn("large-change", parse(r.out)[1], self.show(r))

    def test_test_and_generated_lines_never_change_the_warning_or_level_property(self):
        rng = random.Random(11)
        for it in range(5):
            review = rng.randint(1, 40)
            budget = rng.randint(1, 40)
            base = {"pkg/core.go": lines(review)}
            extra = dict(base)
            for rel in rng.sample(["tests/x_test.py", "pkg/core_test.go"] + list(GENERATED), 3):
                extra[rel] = lines(rng.randint(1, 3000))
            with self.subTest(iteration=it, review=review, budget=budget, extra=sorted(extra)):
                r0 = self.ok(self.risk(*self.change(base), budget=budget))
                r1 = self.ok(self.risk(*self.change(extra), budget=budget))
                self.assertEqual(bool(warnings(r0.out)), review > budget, self.show(r0))
                self.assertEqual(warnings(r1.out), warnings(r0.out), self.show(r1))
                self.assertEqual(r1.out.splitlines()[0], r0.out.splitlines()[0], self.show(r1))


class TestBudgetIndependence(BudgetCase):

    def test_budget_changes_rules_only_by_the_size_rule_and_the_warning_is_never_first_property(self):
        rng = random.Random(29)
        pool = {"pkg/util.go": None, "docs/notes.md": None, "pkg/auth/login.go": None,
                ".github/workflows/ci.yml": None, "tests/t_test.py": None, "dist/out.js": None}
        for it in range(6):
            files = {rel: lines(rng.randint(1, 60)) for rel in rng.sample(sorted(pool), rng.randint(1, 4))}
            answers = safe_answers(**{k: "yes" for k in rng.sample(["user_visible", "persisted"], rng.randint(0, 1))})
            b1, b2 = rng.randint(1, 30), rng.choice([rng.randint(31, 500), 10 ** 9])
            packet = rng.choice([True, False])
            with self.subTest(iteration=it, files=sorted(files), answers=answers, budgets=(b1, b2)):
                repo, feature = self.change(files, answers)
                r1 = self.ok(self.risk(repo, feature, budget=b1, packet=packet))
                r2 = self.ok(self.risk(repo, feature, budget=b2, packet=packet))
                (f1, rules1, _), (f2, rules2, _) = parse(r1.out), parse(r2.out)
                for r, first in ((r1, f1), (r2, f2)):
                    self.assertRegex(first, FIRST_LINE, self.show(r))
                    self.assertFalse(first.startswith(WARN_PREFIX), self.show(r))
                strip = lambda rules: {k: v for k, v in rules.items() if k != "large-change"}
                self.assertEqual(strip(rules1), strip(rules2), (self.show(r1), self.show(r2)))
                if f1.split()[2] == f2.split()[2]:
                    self.assertEqual(f1, f2, "the same level gives the same footer whatever the budget")


class TestBudgetSource(BudgetCase):

    def test_budget_comes_from_flag_then_variable_then_400_and_bad_values_are_refused(self):
        repo, feature = self.change({"pkg/big.go": lines(401)})
        r = self.ok(self.risk(repo, feature))
        self.assertEqual(warnings(r.out), [warning(401, 400)], self.show(r))
        r = self.ok(self.risk(repo, feature, env_budget=""))
        self.assertEqual(warnings(r.out), [warning(401, 400)], "an empty variable counts as unset")
        r = self.ok(self.risk(repo, feature, env_budget="1000"))
        self.assertEqual(warnings(r.out), [], self.show(r))
        r = self.ok(self.risk(repo, feature, env_budget="10"))
        self.assertEqual(warnings(r.out), [warning(401, 10)], self.show(r))
        r = self.ok(self.risk(repo, feature, budget=1000, env_budget="10"))
        self.assertEqual(warnings(r.out), [], "the flag wins over the variable")
        r = self.ok(self.risk(repo, feature, budget=10, env_budget="1000"))
        self.assertEqual(warnings(r.out), [warning(401, 10)], "the flag wins over the variable")
        r = self.ok(self.risk(repo, feature, budget=1000, env_budget="abc"))
        self.assertEqual(warnings(r.out), [], "with the flag given the variable is not read")

        repo, feature = self.change({"pkg/edge.go": lines(400)})
        r = self.ok(self.risk(repo, feature))
        self.assertEqual(warnings(r.out), [], "400 lines are within the default budget")

        bad = ["0", "-1", "1.5", "abc", "+5", " 5", "1e3"]
        for value in bad + [""]:
            with self.subTest(flag=value):
                r = self.code(self.risk(repo, feature, budget=value, log=True), 1)
                self.assertEqual(r.out, "", self.show(r))
                self.has(r.err, "team-guard: risk:", r)
                self.has(r.err, "--pr-budget", r)
        for value in bad:
            with self.subTest(variable=value):
                r = self.code(self.risk(repo, feature, env_budget=value, log=True), 1)
                self.assertEqual(r.out, "", self.show(r))
                self.has(r.err, "team-guard: risk:", r)
                self.has(r.err, BUDGET_ENV, r)
        self.assertEqual(self.risk_events(), [], "a refused budget logs no event")


class TestBudgetPacketAndLog(BudgetCase):

    def test_packet_and_log_record_budget_outcome_and_lines_to_review(self):
        files = {"pkg/core.go": lines(20), "docs/a.md": lines(5), "dist/out.js": lines(300),
                 "tests/big_test.py": lines(50)}
        for budget, over in ((24, "yes"), (25, "no"), (26, "no")):
            with self.subTest(budget=budget):
                repo, feature = self.change(files)
                r = self.ok(self.risk(repo, feature, budget=budget))
                _, _, inputs = parse(r.out)
                self.assertEqual((inputs.get("pr_budget"), inputs.get("over_budget"), inputs.get("review_lines")),
                                 (str(budget), over, "25"), self.show(r))
                self.assertEqual(inputs.get("non_test_lines"), "325", "the existing count is unchanged")
                self.assertEqual(bool(warnings(r.out)), over == "yes", self.show(r))
                names = [l.split(":", 1)[0] for l in r.out.splitlines() if l.startswith("input ")]
                self.assertEqual(names, sorted(names), "inputs stay sorted by name")

                self.ok(self.risk(repo, feature, budget=budget, packet=False, log=True))
                events = [e for e in self.risk_events() if e.get("feature") == feature]
                self.assertEqual(len(events), 1, self.log_path.read_text())
                logged = events[0]["inputs"]
                self.assertEqual(logged.get("over_budget"), over)
                self.assertEqual(logged.get("review_lines"), 25)
                self.assertIsInstance(logged.get("review_lines"), int)
                self.assertNotIn("pr_budget", logged)
                self.assertNotIn("pr_budget", events[0])


class TestSizeInRiskLevel(BudgetCase):

    def test_over_budget_or_many_files_is_medium_and_the_footer_never_mentions_size(self):
        footers = []

        def run(files, answers=None, *args, budget=400):
            r = self.ok(self.risk(*self.change(files, answers), *args, budget=budget))
            first, rules, _ = parse(r.out)
            footers.append(first)
            return r, first, rules

        big = {"pkg/big.go": lines(401)}
        r, first, rules = run(big)
        self.assertEqual(rules, {"large-change": "medium"}, self.show(r))
        self.assertEqual(first, "Merge risk: medium (advisory).", self.show(r))

        r, first, rules = run(big, safe_answers(user_visible="yes"))
        self.assertEqual(rules, {"large-change": "medium", "user-visible": "medium"}, self.show(r))
        self.assertEqual(first, "Merge risk: medium (advisory) — %s." % USER_VISIBLE, self.show(r))

        r, first, rules = run(big, None, "--majors-fixed", "1")
        self.assertEqual(rules, {"large-change": "medium", "majors-fixed": "medium"}, self.show(r))
        self.assertEqual(first, "Merge risk: medium (advisory) — %s." % PROCESS_ONLY, self.show(r))

        r, first, rules = run(big, safe_answers(persisted="yes"))
        self.assertEqual(first, "Merge risk: high (advisory) — %s." % PERSISTED, self.show(r))

        many = {"pkg/m%02d.go" % i: "package pkg\n" for i in range(16)}
        r, first, rules = run(many, budget=10 ** 6)
        self.assertEqual(rules, {"large-change": "medium"}, self.show(r))
        self.assertEqual(first, "Merge risk: medium (advisory).", self.show(r))

        r, first, rules = run({"pkg/m%02d.go" % i: "package pkg\n" for i in range(15)}, budget=10 ** 6)
        self.assertEqual((first, rules), (LOW, {}), self.show(r))

        r, first, rules = run({"pkg/small.go": lines(3)})
        self.assertEqual((first, rules), (LOW, {}), self.show(r))

        for first in footers:
            with self.subTest(footer=first):
                self.assertNotRegex(first, re.compile(r"size|large|lines|\d", re.I))


class TestGeneratedPaths(BudgetCase):

    def test_only_exact_generated_names_and_dist_segments_are_left_out_of_the_lines_to_review(self):
        # A name that merely contains a generated marker is hand-written code a human must read.
        files = {"pkg/fuzz_generated.go": lines(5)}
        for rel in ("x/y_gen.go", "a.gen.ts", "p.pb.go", "m_pb2.py", "zz_generated.deepcopy.go",
                    "dist/bundle.js", "web/dist/app.js"):
            files[rel] = lines(2000)
        repo, feature = self.change(files)
        r = self.ok(self.risk(repo, feature, budget=4))
        _, rules, inputs = parse(r.out)
        self.assertEqual(inputs.get("review_lines"), "5", self.show(r))
        self.assertEqual(warnings(r.out), [warning(5, 4)], self.show(r))
        self.assertEqual(rules.get("large-change"), "medium", self.show(r))


class TestRiskUsage(ToolCase):

    def test_risk_usage_names_the_budget_flag_and_its_environment_variable(self):
        r = self.ok(self.sb.tg(cwd=self.sb.scratch))
        block, inside = [], False
        for line in r.out.splitlines():
            if line.startswith("  team-guard "):
                inside = line.startswith("  team-guard risk")
            elif not line.startswith("    "):
                inside = False
            if inside:
                block.append(line)
        usage = "\n".join(block)
        self.assertIn("--pr-budget", usage, self.show(r))
        self.assertIn(BUDGET_ENV, usage, self.show(r))


if __name__ == "__main__":
    unittest.main()
