from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "myst" / "myst-readiness.sh"


class MystVpnReadinessTests(unittest.TestCase):
    def _run(
        self, response: str, fake_ip_body: str
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            invoked = temp / "online-invoked"
            fake_ip = temp / "ip"
            fake_ip.write_text(
                "#!/bin/sh\nset -eu\n" + textwrap.dedent(fake_ip_body),
                encoding="utf-8",
            )
            fake_wget = temp / "wget"
            fake_wget.write_text(
                "#!/bin/sh\nset -eu\n"
                "[ \"$*\" = '-Y off -q -T 5 -O - http://127.0.0.1:4050/connection' ]\n"
                "printf '%s' \"$FAKE_TEQUIL_RESPONSE\"\n",
                encoding="utf-8",
            )
            for name in ("dig", "nslookup", "host", "curl", "getent"):
                helper = temp / name
                helper.write_text(
                    "#!/bin/sh\nprintf '%s\\n' invoked >>\"$ONLINE_INVOKED\"\nexit 99\n",
                    encoding="utf-8",
                )
                helper.chmod(helper.stat().st_mode | stat.S_IXUSR)
            fake_ip.chmod(fake_ip.stat().st_mode | stat.S_IXUSR)
            fake_wget.chmod(fake_wget.stat().st_mode | stat.S_IXUSR)
            env = {
                "MYST_VPN_ENABLED": "true",
                "FAKE_TEQUIL_RESPONSE": response,
                "ONLINE_INVOKED": str(invoked),
                "PATH": f"{temp_dir}:/usr/bin:/bin",
            }
            result = subprocess.run(
                ["/bin/sh", str(SCRIPT)],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            online_calls = invoked.read_text(encoding="utf-8") if invoked.exists() else ""
            return result, online_calls

    @staticmethod
    def _healthy_ip() -> str:
        return """
        case "$*" in
          '-4 -o addr show dev myst0 scope global')
            echo '17: myst0 inet 10.20.30.40/24 scope global myst0'
            ;;
          '-4 route get 198.51.100.1')
            echo '198.51.100.1 dev myst0 src 10.20.30.40 uid 0'
            ;;
          *) exit 1 ;;
        esac
        """

    def test_accepts_connected_local_state_without_online_probe(self) -> None:
        response = '{"status":"Connected","provider":"secret-provider","identity":"secret-identity","session":"secret-session","location":"secret-location"}'
        result, online_calls = self._run(response, self._healthy_ip())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(online_calls, "")
        for secret in ("secret-provider", "secret-identity", "secret-session", "secret-location"):
            self.assertNotIn(secret, result.stdout + result.stderr)

    def test_rejects_disconnected_status(self) -> None:
        result, online_calls = self._run(
            '{"status":"Disconnected","provider":"private"}', self._healthy_ip()
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(online_calls, "")
        self.assertNotIn("private", result.stdout + result.stderr)

    def test_rejects_malformed_nested_duplicate_or_nonleading_status(self) -> None:
        responses = (
            '{"provider":{"status":"Connected"}}',
            '{"provider":"x","status":"Connected"}',
            '{"status":"Connected"',
            '{"status":"Connectedness"}',
            '{"status":"Connected","status":"Disconnected"}',
        )
        for response in responses:
            with self.subTest(response=response):
                result, online_calls = self._run(response, self._healthy_ip())
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(online_calls, "")

    def test_rejects_missing_address(self) -> None:
        result, _ = self._run(
            '{"status":"Connected"}',
            "case \"$*\" in '-4 -o addr show dev myst0 scope global') exit 0 ;; *) exit 1 ;; esac",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no global IPv4 address", result.stderr)

    def test_rejects_route_device_or_source_mismatch_and_missing_route(self) -> None:
        routes = (
            "198.51.100.1 dev eth0 src 10.20.30.40",
            "198.51.100.1 dev myst0 src 10.20.30.41",
            "",
        )
        for route in routes:
            with self.subTest(route=route):
                body = f"""
                case "$*" in
                  '-4 -o addr show dev myst0 scope global')
                    echo '17: myst0 inet 10.20.30.40/24 scope global myst0'
                    ;;
                  '-4 route get 198.51.100.1')
                    printf '%s\\n' '{route}'
                    ;;
                  *) exit 1 ;;
                esac
                """
                result, online_calls = self._run('{"status":"Connected"}', body)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(online_calls, "")


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
