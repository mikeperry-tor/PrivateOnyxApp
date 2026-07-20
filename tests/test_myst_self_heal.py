from __future__ import annotations

import os
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
        self.process_root = self.root / "proc"
        self.process_root.mkdir()
        self.launcher = self.root / "with-process-identity.sh"
        self.launcher.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "process_root=$1\n"
            "shift\n"
            "mkdir -p \"${process_root}/$$\"\n"
            "fields=S\n"
            "i=1\n"
            "while [ \"${i}\" -le 18 ]; do fields=\"${fields} 0\"; i=$((i + 1)); done\n"
            "printf '%s (health-test) %s %s\\n' \"$$\" \"${fields}\" \"$$\" > \"${process_root}/$$/stat\"\n"
            "exec \"$@\"\n",
            encoding="utf-8",
        )
        self.launcher.chmod(self.launcher.stat().st_mode | stat.S_IXUSR)
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
            self._check_command(target_pid, state=state),
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
            [
                str(self.launcher),
                str(self.process_root),
                "/bin/sh",
                str(SCRIPT),
                "reset",
                str(self.state),
                str(self.process_root),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def _check_command(
        self, target_pid: int, *, state: Path | None = None
    ) -> list[str]:
        return [
            str(self.launcher),
            str(self.process_root),
            "/bin/sh",
            str(SCRIPT),
            "check",
            str(target_pid),
            str(state or self.state),
            str(self.readiness),
            str(self.clock),
            "60",
            str(self.process_root),
        ]

    def _write_process_identity(self, pid: int, start: int) -> None:
        process = self.process_root / str(pid)
        process.mkdir(exist_ok=True)
        fields = ["S", *("0" for _ in range(18)), str(start)]
        (process / "stat").write_text(
            f"{pid} (test process) {' '.join(fields)}\n", encoding="utf-8"
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

    def test_future_timestamp_is_replaced_without_signal(self) -> None:
        target = self._target()
        self._arm(target.pid)
        (self.state / "first-failure-uptime").write_text("200\n")
        self._set_clock(100)
        result = self._run("failure", target.pid)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid failure timestamp was reset", result.stderr)
        self.assertEqual((self.state / "first-failure-uptime").read_text(), "100\n")
        self.assertIsNone(target.poll())

    def test_malformed_uptime_fails_without_signaling(self) -> None:
        target = self._target()
        self._arm(target.pid)
        self.clock.write_text("not-uptime\n", encoding="utf-8")
        result = self._run("failure", target.pid)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("monotonic uptime source is malformed", result.stderr)
        self.assertIsNone(target.poll())

    def test_malformed_armed_state_fails_without_signaling(self) -> None:
        target = self._target()
        self.state.mkdir()
        (self.state / "armed").write_text("unexpected\n", encoding="utf-8")
        result = self._run("failure", target.pid)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("armed state is malformed", result.stderr)
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

    def test_nonregular_failure_state_fails_without_signaling(self) -> None:
        target = self._target()
        self.state.mkdir()
        (self.state / "armed").write_text("armed\n", encoding="utf-8")
        (self.state / "first-failure-uptime").mkdir()
        result = self._run("failure", target.pid)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failure timestamp has an unsafe file type", result.stderr)
        self.assertIsNone(target.poll())

    def test_symlink_state_directory_fails_without_signaling(self) -> None:
        target = self._target()
        outside = self.root / "outside-state"
        outside.mkdir()
        state = self.root / "linked-state"
        state.symlink_to(outside, target_is_directory=True)
        result = self._run("failure", target.pid, state=state)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("state directory has an unsafe file type", result.stderr)
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
        command = self._check_command(target.pid)
        env = {**os.environ, "MYST_VPN_ENABLED": "true", "READINESS_RESULT": "failure"}
        checks = [
            subprocess.Popen(command, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for _ in range(2)
        ]
        diagnostics = "".join(check.communicate(timeout=5)[1] for check in checks)
        target.wait(timeout=2)
        self.assertEqual(diagnostics.count("requesting graceful container restart"), 1)

    def test_reused_pid_stale_lock_is_reclaimed_immediately(self) -> None:
        unrelated = self._target()
        self._write_process_identity(unrelated.pid, 222)
        lock = self.state / ".lock"
        lock.mkdir(parents=True)
        (lock / "owner").write_text(f"{unrelated.pid}:111\n", encoding="utf-8")
        started = time.monotonic()
        result = self._run("success")
        elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(elapsed, 2)
        self.assertIsNone(unrelated.poll())
        self.assertFalse(lock.exists())

    def test_interrupted_empty_lock_is_reclaimed_without_overlap(self) -> None:
        lock = self.state / ".lock"
        lock.mkdir(parents=True)
        started = time.monotonic()
        result = self._run("success")
        elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(elapsed, 0.8)
        self.assertLess(elapsed, 5)
        self.assertFalse(lock.exists())

    def test_failed_signal_rearms_for_a_later_attempt(self) -> None:
        self._arm()
        self._set_clock(0)
        self._run("failure")
        self._set_clock(60)
        result = self._run("failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not signal target PID", result.stderr)
        self.assertEqual((self.state / "armed").read_text(), "armed\n")
        retry = self._run("failure")
        self.assertIn("requesting graceful container restart", retry.stderr)

    def test_entrypoint_shutdown_helper_terminates_and_reaps_both_children(self) -> None:
        helper = ROOT / "myst/child-process-control.sh"
        service_marker = self.root / "service-stopped"
        route_marker = self.root / "route-stopped"
        ready = self.root / "supervisor-ready"
        child = self.root / "controlled-child.sh"
        child.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "marker=$1\n"
            "trap 'printf stopped > \"${marker}\"; exit 0' INT TERM\n"
            "printf ready > \"${marker}.ready\"\n"
            "while true; do sleep 1; done\n",
            encoding="utf-8",
        )
        child.chmod(child.stat().st_mode | stat.S_IXUSR)
        harness = self.root / "shutdown-harness.sh"
        harness.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            ". \"$1\"\n"
            "\"$2\" \"$3\" & svc_pid=$!\n"
            "\"$2\" \"$4\" & route_fix_pid=$!\n"
            "install_shutdown_handler\n"
            "while [ ! -f \"$3.ready\" ] || [ ! -f \"$4.ready\" ]; do sleep 0.02; done\n"
            "printf '%s %s\\n' \"${svc_pid}\" \"${route_fix_pid}\" > \"$5\"\n"
            "wait \"${svc_pid}\"\n",
            encoding="utf-8",
        )
        harness.chmod(harness.stat().st_mode | stat.S_IXUSR)
        supervisor = subprocess.Popen(
            [
                str(harness),
                str(helper),
                str(child),
                str(service_marker),
                str(route_marker),
                str(ready),
            ]
        )
        self.children.append(supervisor)
        deadline = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(ready.exists(), "shutdown harness did not become ready")
        child_pids = [int(value) for value in ready.read_text().split()]
        supervisor.terminate()
        self.assertEqual(supervisor.wait(timeout=3), 0)
        self.assertTrue(service_marker.exists())
        self.assertTrue(route_marker.exists())
        for pid in child_pids:
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)


if __name__ == "__main__":
    unittest.main()
