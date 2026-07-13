from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "myst" / "myst-readiness.sh"


class MystNoVpnReadinessTests(unittest.TestCase):
    def _run(self, fake_ip_body: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_ip = Path(temp_dir) / "ip"
            fake_ip.write_text(
                "#!/bin/sh\nset -eu\n" + textwrap.dedent(fake_ip_body),
                encoding="utf-8",
            )
            fake_ip.chmod(fake_ip.stat().st_mode | stat.S_IXUSR)
            env = {
                "MYST_VPN_ENABLED": "false",
                "PATH": f"{temp_dir}:/usr/bin:/bin",
            }
            return subprocess.run(
                ["/bin/sh", str(SCRIPT)],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

    def test_accepts_default_route_on_non_eth0_interface(self) -> None:
        result = self._run(
            """
            case "$*" in
              'link show myst0') exit 1 ;;
              '-4 route show default')
                echo 'default via 172.28.0.1 dev eth6'
                exit 0
                ;;
              'link show dev eth6') exit 0 ;;
              '-4 -o addr show dev eth6 scope global')
                echo '17: eth6 inet 172.28.0.9/16 scope global eth6'
                exit 0
                ;;
              *) exit 1 ;;
            esac
            """
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_missing_default_route(self) -> None:
        result = self._run(
            """
            case "$*" in
              'link show myst0') exit 1 ;;
              '-4 route show default') exit 0 ;;
              *) exit 1 ;;
            esac
            """
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no non-myst0 IPv4 default route", result.stderr)

    def test_rejects_stale_myst0_even_with_direct_default_route(self) -> None:
        result = self._run(
            """
            case "$*" in
              'link show myst0') exit 0 ;;
              *) exit 1 ;;
            esac
            """
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected myst0 interface", result.stderr)


if __name__ == "__main__":
    unittest.main()
