from __future__ import annotations

import shutil
import subprocess
import unittest

from helpers import SKILL_DIR

NODE = shutil.which("node")


@unittest.skipUnless(NODE, "needs Node.js")
class PageScript(unittest.TestCase):
    def test_pure_helpers(self):
        result = subprocess.run(
            [NODE, "--test", str(SKILL_DIR / "tests" / "js" / "review.test.js")], capture_output=True, text=True
        )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
