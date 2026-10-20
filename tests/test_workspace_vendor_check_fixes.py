"""vendor-check edge cases: a stored patch broken at the pin, an unexpected error, and a
changed upstream license.

A broken patch means the copies can no longer be reproduced from the pin, which is worse
than drift, so it is a cannot-run exit even though the rest of the report still prints. A
license change matters because every copy carries the license text.
"""

import os
import unittest

from test_workspace_vendor_check import LICENSE, VendorCase


class TestVendorCheckEdges(VendorCase):

    def test_a_patch_broken_at_the_pin_is_reported_with_the_rest_and_exits_two(self):
        patch = self.F / "patches" / "beta-x.patch"
        patch.write_text(patch.read_text().replace("-save it to one place", "-save it nowhere at all"))
        r = self.code(self.check(ref=self.pin), 2)
        self.assertEqual(r.out, "alpha: ok\nbeta-x: patch broken at pin\n", self.show(r))
        self.assertTrue(r.err.startswith("vendor-check: "), r.err)
        self.no_traceback(r)

    def test_an_unexpected_error_exits_two_without_a_traceback(self):
        target = self.F / "alpha" / "SKILL.md"
        os.chmod(target, 0)
        try:
            r = self.code(self.check(ref=self.pin), 2)
        finally:
            os.chmod(target, 0o644)
        self.assertTrue(r.err.startswith("vendor-check: "), r.err)
        self.no_traceback(r)

    def test_an_upstream_license_change_counts_as_drift(self):
        self.branch("license", lambda: (self.U / "LICENSE").write_text(LICENSE + "Extra terms.\n"))
        self.sb.git(self.U, "checkout", "-q", "main")
        r = self.code(self.check(ref="license"), 1)
        self.assertEqual(r.out, "alpha: ok\nbeta-x: ok\nLICENSE main: upstream changed\ndrift in 1 skill(s)\n", self.show(r))


if __name__ == "__main__":
    unittest.main()
