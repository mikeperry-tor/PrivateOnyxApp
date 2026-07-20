from __future__ import annotations

import os
import signal
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "myst/myst-healthcheck.sh"


class MystSelfHealTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.clock = self.root / "uptime"
        self.clock.write_text("0.00 100.00\n", encoding="utf-8")
        self.readiness = self.root / "readiness.sh"
        self.readiness.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "if [ \"${READINESS_RESULT:-failure}\" = success ]; then exit 0; fi\n"
            "echo 'mock readiness failed' >&2\n"
            "exit 1\n",
            encoding="utf-8",
        )
        self.readiness.chmod(self.readiness.stat().st_mode | stat.S_IXUSR)
        self.children: list[subprocess.Popen[bytes]] = []

    def tearDown(self) -> None:
        for child in self.children:
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=2)
        self.temporary.cleanup()

    def _target(self) -> subprocess.Popen[bytes]:
        child = subprocess.Popen(["sleep", "120"])
        self.children.append(child)
        return child

    def _run(
        self,
        result: str,
        target_pid: int = 999999,
        *,
        vpn_enabled: str = "true",
        state: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "/bin/sh",
                str(SCRIPT),
                "check",
                str(target_pid),
                str(state or self.state),
                str(self.readiness),
                str(self.clock),
                "60",
            ],
            env={
                **os.environ,
                "MYST_VPN_ENABLED": vpn_enabled,
                "READINESS_RESULT": result,
                "FAKE_API_BODY": "secret-provider secret-identity secret-location",
            },
            text=True,
            capture_output=True,
            check=False,
        )

    def _reset(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/sh", str(SCRIPT), "reset", str(self.state)],
            text=True,
            capture_output=True,
            check=False,
        )

    def _set_clock(self, seconds: int) -> None:
        self.clock.write_text(f"{seconds}.00 100.00\n", encoding="utf-8")

    def _arm(self, target_pid: int = 999999) -> None:
        result = self._run("success", target_pid)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.state / "armed").read_text(), "armed\n")

    def test_reset_removes_only_known_state_files(self) -> None:
        self.state.mkdir()
        (self.state / "armed").write_text("armed\n")
        (self.state / "first-failure-uptime").write_text("10\n")
        unrelated = self.state / "unrelated"
        unrelated.write_text("keep\n")
        result = self._reset()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.state / "armed").exists())
        self.assertFalse((self.state / "first-failure-uptime").exists())
        self.assertEqual(unrelated.read_text(), "keep\n")

    def test_failure_before_success_is_unarmed_and_does_not_signal(self) -> None:
        target = self._target()
        result = self._run("failure", target.pid)
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(target.poll())
        self.assertFalse((self.state / "armed").exists())
        self.assertFalse((self.state / "first-failure-uptime").exists())

    def test_success_arms_and_clears_failure_timestamp(self) -> None:
        self._arm()
        self._set_clock(10)
        self.assertNotEqual(self._run("failure").returncode, 0)
        self.assertTrue((self.state / "first-failure-uptime").exists())
        result = self._run("success")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.state / "first-failure-uptime").exists())

    def test_stable_success_does_not_republish_armed_marker(self) -> None:
        self._arm()
        armed = self.state / "armed"
        original_inode = armed.stat().st_ino
        result = self._run("success")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(armed.stat().st_ino, original_inode)

    def test_repeated_startup_cadence_failures_inside_grace_do_not_signal(self) -> None:
        target = self._target()
        self._arm(target.pid)
        for second in range(0, 60, 5):
            self._set_clock(second)
            self.assertNotEqual(self._run("failure", target.pid).returncode, 0)
            self.assertIsNone(target.poll())
        self.assertEqual((self.state / "first-failure-uptime").read_text(), "0\n")

    def test_continuous_failure_at_grace_signals_once(self) -> None:
        target = self._target()
        self._arm(target.pid)
        self._set_clock(10)
        self.assertNotEqual(self._run("failure", target.pid).returncode, 0)
        self._set_clock(70)
        first = self._run("failure", target.pid)
        self.assertNotEqual(first.returncode, 0)
        target.wait(timeout=2)
        self.assertIn("requesting graceful container restart", first.stderr)
        self.assertEqual((self.state / "armed").read_text(), "signaled\n")
        second = self._run("failure", target.pid)
        self.assertNotIn("requesting graceful container restart", second.stderr)

    def test_success_between_failures_starts_a_fresh_window(self) -> None:
        target = self._target()
        self._arm(target.pid)
        self._set_clock(10)
        self._run("failure", target.pid)
        self._set_clock(69)
        self.assertEqual(self._run("success", target.pid).returncode, 0)
        self._set_clock(70)
        self._run("failure", target.pid)
        self._set_clock(129)
        self._run("failure", target.pid)
        self.assertIsNone(target.poll())

    def test_no_vpn_never_creates_state_or_signals(self) -> None:
        target = self._target()
        for result in ("failure", "success"):
            completed = self._run(result, target.pid, vpn_enabled="false")
            self.assertEqual(completed.returncode, 0 if result == "success" else 1)
        self.assertIsNone(target.poll())
        self.assertFalse(self.state.exists())

    def test_malformed_timestamp_is_replaced_without_signal(self) -> None:
        target = self._target()
        self._arm(target.pid)
        (self.state / "first-failure-uptime").write_text("not-a-time\n")
        self._set_clock(100)
        result = self._run("failure", target.pid)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("malformed failure timestamp was reset", result.stderr)
        self.assertNotIn("secret-provider", result.stdout + result.stderr)
        self.assertEqual((self.state / "first-failure-uptime").read_text(), "100\n")
        self.assertIsNone(target.poll())

    def test_unsafe_state_types_fail_without_signaling(self) -> None:
        target = self._target()
        self.state.mkdir()
        outside = self.root / "outside"
        outside.write_text("armed\n")
        (self.state / "armed").symlink_to(outside)
        result = self._run("failure", target.pid)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe file type", result.stderr)
        self.assertIsNone(target.poll())

    def test_missing_armed_state_is_safe_and_clears_regular_timestamp(self) -> None:
        self.state.mkdir()
        (self.state / "first-failure-uptime").write_text("0\n")
        result = self._run("failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.state / "first-failure-uptime").exists())

    def test_overlapping_expired_checks_emit_one_signal_diagnostic(self) -> None:
        target = self._target()
        self._arm(target.pid)
        self._set_clock(0)
        self._run("failure", target.pid)
        self._set_clock(60)
        command = [
            "/bin/sh",
            str(SCRIPT),
            "check",
            str(target.pid),
            str(self.state),
            str(self.readiness),
            str(self.clock),
            "60",
        ]
        env = {**os.environ, "MYST_VPN_ENABLED": "true", "READINESS_RESULT": "failure"}
        checks = [
            subprocess.Popen(command, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for _ in range(2)
        ]
        diagnostics = "".join(check.communicate(timeout=5)[1] for check in checks)
        target.wait(timeout=2)
        self.assertEqual(diagnostics.count("requesting graceful container restart"), 1)


if __name__ == "__main__":
    unittest.main()
