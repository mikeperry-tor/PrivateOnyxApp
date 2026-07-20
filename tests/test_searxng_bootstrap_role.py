from __future__ import annotations

import unittest
from pathlib import Path

from searxng.patches.bootstrap_role import is_resource_tracker_cmdline


ROOT = Path(__file__).resolve().parents[1]


class SearxngBootstrapRoleTests(unittest.TestCase):
    def test_tracker_rule_remote_fetch_is_disabled(self) -> None:
        settings = (ROOT / "searxng/core-config/settings.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("plugins: {}", settings)
        self.assertNotIn("tracker_url_remover", settings)

    def test_accepts_only_exact_resource_tracker_command(self) -> None:
        self.assertTrue(
            is_resource_tracker_cmdline(
                b"/usr/local/bin/python3\0-c\0"
                b"from multiprocessing.resource_tracker import main;main(10)\0"
            )
        )
        for cmdline in (
            b"python3\0-c\0from multiprocessing.spawn import spawn_main;spawn_main()\0",
            b"python3\0-c\0from multiprocessing.resource_tracker import main;main(x)\0",
            b"python3\0-m\0searx.webapp\0",
            b"other\0-c\0from multiprocessing.resource_tracker import main;main(10)\0",
        ):
            with self.subTest(cmdline=cmdline):
                self.assertFalse(is_resource_tracker_cmdline(cmdline))


if __name__ == "__main__":
    unittest.main()
