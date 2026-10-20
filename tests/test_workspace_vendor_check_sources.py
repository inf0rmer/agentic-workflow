"""vendor-check with more than one upstream source.

Each skill names its source; every source has its own pin, URL and license. The fake
upstreams here are local repos, so nothing is fetched.
"""

import json
import unittest

from test_workspace_vendor_check import DATE, VendorCase, note
from wsskills import VENDOR_CHECK, insert_note

CC_LICENSE = "Creative Commons Attribution-ShareAlike 4.0\n"
GAMMA = "plugins/gamma/skills/gamma"


class TwoSources(VendorCase):

    @property
    def main_url(self):
        return str(self.U)

    def setUp(self):
        super().setUp()
        self.U2 = self.sb.tmp / "upstream2"
        self.U2.mkdir()
        g = lambda *a: self.sb.git(self.U2, *a, env=self.env)
        g("init", "-q", "-b", "main")
        (self.U2 / "LICENSE").write_text(CC_LICENSE)
        (self.U2 / GAMMA).mkdir(parents=True)
        (self.U2 / GAMMA / "SKILL.md").write_text("---\nname: gamma\ndescription: g\n---\n\n# Gamma\n")
        g("add", "-A")
        g("commit", "-qm", "pin")
        self.pin2 = g("rev-parse", "HEAD")
        g("checkout", "-q", "-b", "relicensed")
        (self.U2 / "LICENSE").write_text(CC_LICENSE + "Changed.\n")
        g("commit", "-qam", "relicense")
        g("checkout", "-q", "main")
        gamma = self.F / "gamma"
        gamma.mkdir()
        (gamma / "SKILL.md").write_text(insert_note((self.U2 / GAMMA / "SKILL.md").read_text(),
                                                    note(GAMMA, self.pin2, False, "gamma", str(self.U2), "CC-BY-SA-4.0")))
        (gamma / "LICENSE").write_text(CC_LICENSE)
        data = json.loads((self.F / "UPSTREAM.json").read_text())
        data["sources"]["other"] = {"url": str(self.U2), "sha": self.pin2, "date": "2026-10-15",
                                    "license": "CC-BY-SA-4.0"}
        data["skills"]["gamma"] = {"source": "other", "upstream_path": GAMMA, "status": "as-is"}
        self.upstream_json(data)

    def run_check(self, *args):
        return self.sb.run([str(VENDOR_CHECK), "--skills-dir", str(self.F)] + list(args))

    def test_two_sources_report_together_sorted(self):
        r = self.ok(self.run_check())
        self.assertEqual(r.out, "alpha: ok\nbeta-x: ok\ngamma: ok\nno drift\n", self.show(r))

    def test_source_limits_the_run_to_one_source(self):
        r = self.ok(self.run_check("--source", "other"))
        self.assertEqual(r.out, "gamma: ok\nno drift\n", self.show(r))
        r = self.ok(self.run_check("--source", "main"))
        self.assertEqual(r.out, "alpha: ok\nbeta-x: ok\nno drift\n", self.show(r))

    def test_upstream_and_ref_need_a_source_when_there_are_two(self):
        for extra in (["--upstream", str(self.U2)], ["--ref", "main"]):
            with self.subTest(extra=extra):
                r = self.code(self.run_check(*extra), 2)
                self.assertEqual(r.out, "")
                self.assertIn("--source", r.err)
        r = self.ok(self.run_check("--source", "other", "--upstream", str(self.U2), "--ref", self.pin2))
        self.assertEqual(r.out, "gamma: ok\nno drift\n", self.show(r))

    def test_a_changed_license_names_its_source(self):
        r = self.code(self.run_check("--source", "other", "--ref", "relicensed"), 1)
        self.assertEqual(r.out, "gamma: ok\nLICENSE other: upstream changed\ndrift in 1 skill(s)\n", self.show(r))

    def test_an_unknown_source_is_a_cannot_run(self):
        r = self.code(self.run_check("--source", "nope"), 2)
        self.assertEqual(r.out, "")
        self.assertTrue(r.err.startswith("vendor-check: "), r.err)

    def test_a_provenance_line_with_the_wrong_license_is_local_drift(self):
        f = self.F / "gamma" / "SKILL.md"
        f.write_text(f.read_text().replace("CC-BY-SA-4.0", "MIT"))
        r = self.code(self.run_check("--source", "other"), 1)
        self.assertIn("gamma: local drift\n  differs: SKILL.md", r.out)


if __name__ == "__main__":
    unittest.main()
