from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tor" / "healthcheck.py"
SPEC = importlib.util.spec_from_file_location("tor_healthcheck", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
tor_health = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tor_health)


class TorHealthcheckTests(unittest.TestCase):
    def test_bootstrap_progress_is_exact_and_bounded(self) -> None:
        reply = [
            b'250-status/bootstrap-phase=NOTICE BOOTSTRAP PROGRESS=73 TAG=loading',
            b"250 OK",
        ]
        self.assertEqual(tor_health.parse_bootstrap_progress(reply), 73)

        for invalid in (
            [b"250-status/bootstrap-phase=NOTICE BOOTSTRAP", b"250 OK"],
            [b"250-PROGRESS=1 PROGRESS=2", b"250 OK"],
            [b"250-PROGRESS=101", b"250 OK"],
        ):
            with self.subTest(invalid=invalid), self.assertRaises(
                tor_health.HealthError
            ):
                tor_health.parse_bootstrap_progress(invalid)


if __name__ == "__main__":
    unittest.main()
