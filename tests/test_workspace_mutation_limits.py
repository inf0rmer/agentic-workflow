"""mutation-check's hard limits: free disk, worker count, temp cleanup, time box and sampling.

Mutation tools can fill a disk, start one worker per core and leave temp trees behind, so
the wrapper bounds every run and always cleans up after itself. The tools are replaced by
fakes on PATH, so nothing is downloaded and a run takes seconds.
"""

import os
import re
import signal
import subprocess
import unittest
from pathlib import Path

from wstools import ToolCase, MC, FakeTools, FifoWaiter, arg_after

GO_LINES = 12
MUTANT = re.compile(r"(CONDITIONALS_BOUNDARY|ARITHMETIC_BASE)\s+(calc\d?/calc\.go):(\d+)")


def go_file(pkg, n=GO_LINES):
    return "package %s\n" % pkg + "".join("var v%d = %d > 3\n" % (i, i) for i in range(1, n))


class MCCase(ToolCase):

    def setUp(self):
        super().setUp()
        self.fake = FakeTools(self.sb.tmp / "fake")
        self.tmp_root = self.sb.tmp / "t"
        self.tmp_root.mkdir()
        self.grandchildren = []
        self.addCleanup(self._kill_grandchildren)

    def _kill_grandchildren(self):
        gp = Path(self.fake.cfg.get("grandchild_pid", "/nonexistent"))
        pids = list(self.grandchildren) + ([int(gp.read_text())] if gp.exists() else [])
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def mc_env(self, **extra):
        env = {"PATH": self.fake.path_env(self.sb.env), "TMPDIR": str(self.tmp_root), "MUTATION_MIN_FREE_GB": "0"}
        env.update({k: str(v) for k, v in extra.items()})
        return env

    def repo(self, pkgs=("calc",), ts_lines=0):
        """A repo whose feature branch adds one Go file per package (and optionally one TS file)."""
        repo = self.sb.tmp / "mc"
        repo.mkdir()
        self.sb.git(repo, "init", "-q", "-b", "main")
        (repo / "go.mod").write_text("module example.com/mut\n\ngo 1.22\n")
        (repo / "package.json").write_text("{}\n")
        self.sb.git(repo, "add", "-A")
        self.sb.git(repo, "commit", "-qm", "base")
        self.sb.git(repo, "checkout", "-q", "-b", "feat")
        for p in pkgs:
            (repo / p).mkdir()
            (repo / p / "calc.go").write_text(go_file(p))
        if ts_lines:
            (repo / "src").mkdir()
            (repo / "src" / "f.ts").write_text("".join("export const f%d = (a: number) => a > %d;\n" % (i, i)
                                                       for i in range(ts_lines)))
        self.sb.git(repo, "add", "-A")
        self.sb.git(repo, "commit", "-qm", "feat")
        return repo

    def mc(self, repo, *args, env=None):
        return self.sb.run([str(MC), "--base", "main", *args], cwd=repo, env=env or self.mc_env())

    def run_dirs(self):
        return sorted(p.name for p in self.tmp_root.iterdir() if p.name.startswith("mutation-check-"))

    def lived(self, pkg, lines=range(1, GO_LINES + 1)):
        return {pkg: ["LIVED CONDITIONALS_BOUNDARY at calc.go:%d:9" % n for n in lines] +
                     ["LIVED ARITHMETIC_BASE at calc.go:%d:5" % n for n in lines]}


class TestFreeDisk(MCCase):

    def test_run_is_refused_below_the_free_disk_floor(self):
        repo = self.repo()
        r = self.code(self.mc(repo, "--go-only", "--min-free-gb", "999999"), 3)
        self.assertRegex(r.err, r"mutation-check: refused: \S+ has \d+\.\d GB free, needs 999999 GB")
        self.assertEqual(self.fake.calls(only_tools=False), [], "no go or npx process is started")
        self.assertEqual(self.run_dirs(), [], "no temp dir is created")
        r = self.code(self.mc(repo, "--go-only", env=self.mc_env(MUTATION_MIN_FREE_GB="999999")), 3)
        self.assertEqual(self.fake.calls(only_tools=False), [])

        r = self.ok(self.mc(repo, "--go-only", "--dry-run", "--min-free-gb", "999999"))
        free = [l for l in r.out.splitlines() if re.match(r"^free disk: \S.* \d+\.\d GB$", l)]
        self.assertEqual(len(free), 2, self.show(r))
        self.assertTrue(any(str(self.tmp_root) in l for l in free), free)
        self.assertIn("unleash", r.out)
        self.assertEqual(self.fake.calls(only_tools=False), [])


class TestWorkers(MCCase):

    def test_tools_get_an_explicit_worker_count_and_run_one_at_a_time(self):
        repo = self.repo(pkgs=("calc", "calc2", "calc3"), ts_lines=3)
        cases = [((), {}, "2"), (("--max-concurrency", "3"), {}, "3"),
                 ((), {"MUTATION_MAX_CONCURRENCY": "4"}, "4"),
                 (("--max-concurrency", "3"), {"MUTATION_MAX_CONCURRENCY": "4"}, "3")]
        for args, env, want in cases:
            with self.subTest(args=args, env=env):
                self.fake.log.unlink(missing_ok=True)
                self.mc(repo, *args, env=self.mc_env(**env))
                go = self.fake.calls("go")
                npx = self.fake.calls("npx")
                self.assertEqual(len(go), 3, go)
                self.assertEqual([arg_after(c["argv"], "--workers") for c in go], [want] * 3)
                self.assertEqual(len(npx), 1, npx)
                self.assertEqual(arg_after(npx[0]["argv"], "--concurrency"), want)
                self.assertFalse(self.fake.overlapped(), "two tool processes ran at once")

    def test_invalid_limit_values_stop_before_anything_runs(self):
        repo = self.repo()
        cases = [(("--max-concurrency", "0"), {}, "--max-concurrency"), (("--sample", "-1"), {}, "--sample"),
                 (("--time-box", "soon"), {}, "--time-box"), (("--min-free-gb", "1.5"), {}, "--min-free-gb"),
                 (("--seed", "x"), {}, "--seed"),
                 ((), {"MUTATION_MIN_FREE_GB": "abc"}, "MUTATION_MIN_FREE_GB"),
                 ((), {"MUTATION_MAX_CONCURRENCY": "-2"}, "MUTATION_MAX_CONCURRENCY"),
                 ((), {"MUTATION_TIME_BOX_MIN": "x"}, "MUTATION_TIME_BOX_MIN"),
                 ((), {"MUTATION_SAMPLE": "many"}, "MUTATION_SAMPLE")]
        for args, env, name in cases:
            with self.subTest(setting=name, args=args):
                r = self.code(self.mc(repo, "--go-only", *args, env=self.mc_env(**env)), 1)
                self.has(r.err, name, r)
                self.assertEqual(self.fake.calls(only_tools=False), [])
                self.assertEqual(self.run_dirs(), [])


class TestCleanup(MCCase):

    def _watch(self, name):
        w = FifoWaiter(self.sb.tmp / ("%s.fifo" % name))
        self.addCleanup(w.release)
        return w

    def _assert_clean_run(self, pid=None):
        calls = self.fake.calls()
        self.assertTrue(calls, "the tool was started")
        td = calls[0]["env"]["TMPDIR"]
        pattern = r"^%s/mutation-check-(\d+)$" % re.escape(str(self.tmp_root))
        self.assertRegex(td or "", pattern, "the tool's temp files go to the run's own dir")
        if pid is not None:
            self.assertEqual(td, "%s/mutation-check-%d" % (self.tmp_root, pid))
        for k in ("TMP", "TEMP", "GOTMPDIR"):
            self.assertEqual(calls[0]["env"][k], td, k)
        self.assertFalse(os.path.exists(td), "the run's temp dir is removed")

    def test_temp_dirs_and_tool_processes_are_cleaned_up_on_every_exit(self):
        repo = self.repo()
        dead = subprocess.Popen(["true"])
        dead.wait()
        stale = self.tmp_root / ("mutation-check-%d" % dead.pid)
        stale.mkdir()
        (stale / "junk").write_text("x")
        live = self.tmp_root / ("mutation-check-%d" % os.getpid())
        live.mkdir()

        for path in ("success", "error", "time box"):
            with self.subTest(exit_path=path):
                self.fake.log.unlink(missing_ok=True)
                gc = self._watch("gc-" + path.replace(" ", "-"))
                self.fake.set(grandchild_fifo=gc.path, grandchild_pid=str(self.sb.tmp / "gc.pid"),
                              exit=1 if path == "error" else 0, sleep=20 if path == "time box" else 0.2,
                              lines=self.lived("calc", [2]))
                env = self.mc_env(MUTATION_TIME_BOX_SEC=2, MUTATION_KILL_GRACE_SEC=1) if path == "time box" \
                    else self.mc_env()
                self.mc(repo, "--go-only", env=env)
                self._assert_clean_run()
                self.assertTrue(gc.closed(10), "the tool's own child process is gone")
                self.assertFalse(stale.exists(), "a dir left by a dead run is removed at start")
                self.assertTrue(live.exists(), "a dir of a live process is kept")

        for sig, code in ((signal.SIGINT, 130), (signal.SIGTERM, 143)):
            with self.subTest(signal=sig.name):
                self.fake.log.unlink(missing_ok=True)
                gc = self._watch("gc-" + sig.name)
                ready = self._watch("ready-" + sig.name)
                self.fake.set(grandchild_fifo=gc.path, ready_fifo=ready.path, sleep=30, exit=0)
                env = dict(self.sb.env, **self.mc_env())
                p = subprocess.Popen([str(MC), "--base", "main", "--go-only"], cwd=str(repo), env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    self.assertTrue(ready.ready(30), "the fake tool started")
                    p.send_signal(sig)
                    p.communicate(timeout=30)
                finally:
                    self.fake.set(ready_fifo=None)
                    if p.poll() is None:
                        p.kill()
                        p.communicate()
                self.assertEqual(p.returncode, code)
                self._assert_clean_run(p.pid)
                self.assertTrue(gc.closed(10), "the tool's own child process is gone")

        ts = self.sb.tmp / "ts"
        ts.mkdir()
        for pre in (False, True):
            with self.subTest(stryker_tmp_existed=pre):
                repo_ts = self.sb.tmp / "ts" / ("r%d" % pre)
                repo_ts.mkdir()
                self.sb.git(repo_ts, "init", "-q", "-b", "main")
                (repo_ts / "package.json").write_text("{}\n")
                self.sb.git(repo_ts, "add", "-A")
                self.sb.git(repo_ts, "commit", "-qm", "base")
                self.sb.git(repo_ts, "checkout", "-q", "-b", "feat")
                (repo_ts / "src").mkdir()
                (repo_ts / "src" / "f.ts").write_text("export const f = (a: number) => a > 3;\n")
                self.sb.git(repo_ts, "add", "-A")
                self.sb.git(repo_ts, "commit", "-qm", "feat")
                if pre:
                    (repo_ts / ".stryker-tmp" / "keep").mkdir(parents=True)
                self.fake.log.unlink(missing_ok=True)
                self.fake.set(grandchild_fifo=None, ready_fifo=None, sleep=0.1)
                self.mc(repo_ts, "--ts-only")
                self.assertTrue(self.fake.calls("npx"), "Stryker was started")
                if pre:
                    self.assertTrue((repo_ts / ".stryker-tmp" / "keep").is_dir(), "an existing .stryker-tmp is kept")
                else:
                    self.assertFalse((repo_ts / ".stryker-tmp").exists(), "the run's .stryker-tmp is removed")
                self._assert_clean_run()

    def test_no_run_leaves_a_dir_or_overlapping_tools_behind_property(self):
        repo = self.repo(pkgs=("calc", "calc2", "calc3"))
        for path in ("success", "error", "time box"):
            with self.subTest(exit_path=path):
                self.fake.log.unlink(missing_ok=True)
                self.fake.set(exit=1 if path == "error" else 0, sleep_by_pkg={"calc2": 20} if path == "time box" else {},
                              sleep=0.3)
                env = self.mc_env(MUTATION_TIME_BOX_SEC=2, MUTATION_KILL_GRACE_SEC=1) if path == "time box" \
                    else self.mc_env()
                self.mc(repo, "--go-only", env=env)
                self.assertTrue(self.fake.calls("go"))
                self.assertTrue(all(re.match(r"^%s/mutation-check-\d+$" % re.escape(str(self.tmp_root)),
                                             c["env"]["TMPDIR"] or "") for c in self.fake.calls("go")))
                self.assertEqual(self.run_dirs(), [])
                self.assertFalse(self.fake.overlapped())


class TestTimeBox(MCCase):

    def test_time_box_stops_the_run_and_exits_four(self):
        repo = self.repo(pkgs=("calc", "calc2"))
        lines = dict(self.lived("calc", [3]), **self.lived("calc2", [4]))
        self.fake.set(lines=lines, sleep_by_pkg={"calc2": 20}, sleep=0.1)
        r = self.code(self.mc(repo, "--go-only", env=self.mc_env(MUTATION_TIME_BOX_SEC=2, MUTATION_KILL_GRACE_SEC=1)), 4)
        self.assertIn("INCOMPLETE: time box of 2 seconds reached", r.out.splitlines(), self.show(r))
        self.has(r.out, "Go mutants on changed lines:", r)
        self.assertEqual(self.run_dirs(), [])

        self.fake.set(sleep_by_pkg={}, sleep=0.1)
        r = self.ok(self.mc(repo, "--go-only", "--time-box", "15"))
        self.assertTrue(MUTANT.search(r.out), "survivors are reported and the run still exits 0\n" + self.show(r))
        self.lacks(r.out, "INCOMPLETE", r)


class TestSampling(MCCase):

    def test_sampling_limits_changed_lines_the_same_way_for_a_seed(self):
        repo = self.repo(pkgs=("calc", "calc2"))
        self.fake.set(lines=dict(self.lived("calc"), **self.lived("calc2")), sleep=0)
        m = 2 * GO_LINES
        r1 = self.ok(self.mc(repo, "--go-only", "--sample", "3"))
        self.assertIn("sampled 3 of %d changed lines (seed 1)" % m, r1.out.splitlines(), self.show(r1))
        found = [(g[1], int(g[2])) for g in MUTANT.findall(r1.out)]
        self.assertTrue(0 < len(found) <= 3, found)
        self.assertEqual(len(found), len(set(found)), "at most one mutant per sampled line")
        r2 = self.ok(self.mc(repo, "--go-only", "--sample", "3", "--seed", "1"))
        self.assertEqual(r2.out, r1.out, "the same seed picks the same lines")
        r3 = self.ok(self.mc(repo, "--go-only", "--sample", "3", "--seed", "2"))
        self.assertIn("sampled 3 of %d changed lines (seed 2)" % m, r3.out.splitlines())

        self.fake.log.unlink(missing_ok=True)
        self.ok(self.mc(repo, "--go-only", "--sample", "1"))
        self.assertEqual(len(self.fake.calls("go")), 1, "only the package holding the sampled line is run")

        r = self.ok(self.mc(repo, "--go-only", "--sample", "50"))
        self.lacks(r.out, "sampled", r)
        self.assertEqual(len(MUTANT.findall(r.out)), 2 * m, "without sampling every surviving mutant is reported")
        r = self.ok(self.mc(repo, "--go-only", env=self.mc_env(MUTATION_SAMPLE="0")))
        self.lacks(r.out, "sampled", r)

        ts = self.sb.tmp / "tsr"
        ts.mkdir()
        self.sb.git(ts, "init", "-q", "-b", "main")
        (ts / "package.json").write_text("{}\n")
        self.sb.git(ts, "add", "-A")
        self.sb.git(ts, "commit", "-qm", "base")
        self.sb.git(ts, "checkout", "-q", "-b", "feat")
        (ts / "src").mkdir()
        (ts / "src" / "f.ts").write_text("".join("export const f%d = %d;\n" % (i, i) for i in range(10)))
        self.sb.git(ts, "add", "-A")
        self.sb.git(ts, "commit", "-qm", "feat")
        r = self.ok(self.mc(ts, "--ts-only", "--dry-run", "--sample", "3"))
        self.assertIn("sampled 3 of 10 changed lines (seed 1)", r.out.splitlines(), self.show(r))
        mutate = arg_after(r.out.split(), "--mutate")
        self.assertIsNotNone(mutate, self.show(r))
        spans = []
        for part in mutate.split(","):
            m_ = re.match(r"^src/f\.ts:(\d+)(?:-(\d+))?$", part)
            self.assertTrue(m_, part)
            a, b = int(m_.group(1)), int(m_.group(2) or m_.group(1))
            spans.append((a, b))
        covered = [n for a, b in spans for n in range(a, b + 1)]
        self.assertEqual(len(covered), 3, spans)
        self.assertTrue(all(1 <= n <= 10 for n in covered), spans)
        for (a1, b1), (a2, b2) in zip(spans, spans[1:]):
            self.assertLess(b1 + 1, a2, "adjacent lines are merged into one range")
        r = self.ok(self.mc(ts, "--ts-only", "--dry-run", "--sample", "10"))
        self.lacks(r.out, "sampled", r)
        self.assertEqual(arg_after(r.out.split(), "--mutate"), "src/f.ts")


if __name__ == "__main__":
    unittest.main()
