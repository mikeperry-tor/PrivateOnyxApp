from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "myst/route-reconciliation.sh"


class MystRouteReconciliationTests(unittest.TestCase):
    def _helpers(self, directory: Path) -> dict[str, str]:
        state = directory / "routes"
        calls = directory / "replacements"
        fake_ip = directory / "ip"
        fake_ip.write_text(
            "#!/bin/sh\nset -eu\n"
            + textwrap.dedent(
                """
                if [ "$1 $2 $3" = "-4 route show" ] && [ "$4" = "exact" ]; then
                  target="$5"
                  rendered="${target%/32}"
                  awk -v target="$target" -v rendered="$rendered" \
                    '$1 == target || $1 == rendered {print; found=1} END {exit(found ? 0 : 1)}' "$ROUTE_STATE"
                  exit $?
                fi
                if [ "$1 $2 $3" = "-4 route replace" ]; then
                  target="$4"
                  gateway="$6"
                  device="$8"
                  rendered="${target%/32}"
                  awk -v target="$target" -v rendered="$rendered" \
                    '$1 != target && $1 != rendered {print}' "$ROUTE_STATE" >"$ROUTE_STATE.tmp"
                  printf '%s via %s dev %s\n' "${target%/32}" "$gateway" "$device" >>"$ROUTE_STATE.tmp"
                  mv "$ROUTE_STATE.tmp" "$ROUTE_STATE"
                  printf '%s\n' "$target" >>"$ROUTE_CALLS"
                  exit 0
                fi
                exit 1
                """
            ),
            encoding="utf-8",
        )
        fake_getent = directory / "getent"
        fake_getent.write_text(
            "#!/bin/sh\nset -eu\n"
            "[ \"$1 $2\" = 'ahostsv4 broker.example' ]\n"
            "printf '192.0.2.20 STREAM broker.example\\n'\n",
            encoding="utf-8",
        )
        for helper in (fake_ip, fake_getent):
            helper.chmod(helper.stat().st_mode | stat.S_IXUSR)
        state.write_text("", encoding="utf-8")
        return {
            "PATH": f"{directory}:/usr/bin:/bin",
            "ROUTE_STATE": str(state),
            "ROUTE_CALLS": str(calls),
            "DOCKER_BRIDGE_GW": "172.18.0.1",
            "DOCKER_BRIDGE_DEV": "eth0",
            "MYST_ROUTE_EXEMPT_HOSTS": "broker.example",
            "MYST_ROUTE_EXEMPT_CIDRS": "10.10.0.0/16",
        }

    def test_second_reconciliation_performs_no_writes_or_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {**os.environ, **self._helpers(root)}
            command = (
                f". {SCRIPT}; apply_route_exemptions; "
                "printf '%s\\n' SECOND; apply_route_exemptions"
            )
            result = subprocess.run(
                ["/bin/sh", "-c", command],
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            first, second = result.stdout.split("SECOND\n", 1)
            self.assertEqual(first.count("Route exemption updated:"), 2)
            self.assertEqual(second, "")
            calls = Path(env["ROUTE_CALLS"]).read_text(encoding="utf-8").splitlines()
            self.assertEqual(calls, ["192.0.2.20/32", "10.10.0.0/16"])

    def test_drifted_gateway_is_repaired_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {**os.environ, **self._helpers(root)}
            Path(env["ROUTE_STATE"]).write_text(
                "192.0.2.20 via 172.18.0.99 dev eth0\n"
                "10.10.0.0/16 via 172.18.0.1 dev eth0\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["/bin/sh", "-c", f". {SCRIPT}; apply_route_exemptions"],
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(result.stdout.count("Route exemption updated:"), 1)
            calls = Path(env["ROUTE_CALLS"]).read_text(encoding="utf-8").splitlines()
            self.assertEqual(calls, ["192.0.2.20/32"])


if __name__ == "__main__":
    unittest.main()
