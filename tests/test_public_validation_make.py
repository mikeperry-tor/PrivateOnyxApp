from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform in ("linux", "darwin"), "requires supported Make platforms")
class PublicValidationRecursionTests(unittest.TestCase):
    def test_public_settings_cross_recursive_edges_without_losing_image_override(self):
        source = (ROOT / "Makefile").read_text()
        guard = source.split("VERSION_FILE ?=", 1)[0]

        def recursive_recipe(target):
            lines = source.split("\n" + target + ":", 1)[1].splitlines()[1:]
            recipe = []
            for line in lines:
                if not line.startswith("\t"):
                    break
                if "$(MAKE)" in line:
                    recipe.append(line)
            self.assertTrue(recipe, target)
            return "\n".join(recipe) + "\n"

        leaves = ("help", "test-patch-images", "test-obscura-image", "test-tor-image",
                  "test-opensearch-image", "test-security-images")
        with tempfile.TemporaryDirectory(prefix="public-validation-make-") as directory:
            root = Path(directory)
            trace = root / "trace.jsonl"
            recorder = root / "record.py"
            recorder.write_text("import json,sys\nwith open('trace.jsonl','a') as stream: "
                                "stream.write(json.dumps(sys.argv[1:])+'\\n')\n")
            makefile = guard
            for target in ("check", "check-upgrade", "test-all-images"):
                makefile += target + ":\n" + recursive_recipe(target) + "\n"
            for target in leaves:
                makefile += (target + ":\n\t@" + shlex.quote(sys.executable) + " record.py "
                             + target + ' "$(ENV_FILE)" "$(MYST_IMAGE)"\n')
            (root / "Makefile").write_text(makefile)
            environment = dict(os.environ)
            for key in ("ENV_FILE", "MAKEFLAGS", "MAKEOVERRIDES", "MFLAGS", "MAKELEVEL", "MAKE"):
                environment.pop(key, None)
            for goal in ("check", "check-upgrade"):
                for assignments in ([], ["ENV_FILE=synthetic-private.env"]):
                    with self.subTest(goal=goal, assignments=assignments):
                        trace.write_text("")
                        result = subprocess.run(
                            ["make", "--no-print-directory", goal,
                             "MYST_IMAGE=caller/explicit:retained", *assignments],
                            cwd=root, env=environment, capture_output=True, text=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        rows = [json.loads(line) for line in trace.read_text().splitlines()]
                        self.assertEqual({row[0] for row in rows}, {"help"} if goal == "check" else set(leaves))
                        for target, settings, image in rows:
                            self.assertEqual(settings, ".env.wrapper.example", target)
                            self.assertEqual(image, "caller/explicit:retained", target)


if __name__ == "__main__":
    unittest.main()
