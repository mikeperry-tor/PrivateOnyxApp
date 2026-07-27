from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR = ROOT / "tests/fixtures/onyx_supervisord.conf"
ENTRYPOINT = ROOT / "onyx/background_entrypoint.py"
WATCHDOG = ROOT / "onyx/beat_liveness_watchdog.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BackgroundSupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load("background_entrypoint_under_test", ENTRYPOINT)

    def _derive(self, slack: bool = False, discord: bool = False):
        env = {
            "ENABLE_CRAFT": "false",
            "ONYX_AGENT_SLACK_BOT": str(slack).lower(),
            "ONYX_AGENT_DISCORD_BOT": str(discord).lower(),
        }
        with patch.dict(os.environ, env, clear=False):
            return self.module.derive_config(SUPERVISOR)

    def test_default_has_six_workers_no_monitoring_craft_or_bots(self) -> None:
        config = self._derive()
        sections = set(config.sections())
        for removed in (
            "celery_worker_monitoring",
            "celery_worker_scheduled_tasks",
            "slack_bot",
            "discord_bot",
        ):
            self.assertNotIn(f"program:{removed}", sections)
        workers = [s for s in sections if s.startswith("program:celery_worker_")]
        self.assertEqual(len(workers), 6)
        for section in workers:
            command = config.get(section, "command")
            self.assertEqual(command.count("--without-heartbeat"), 1)
            self.assertEqual(command.count("--without-gossip"), 1)
        self.assertIn(
            "-Q docprocessing,port",
            config.get("program:celery_worker_docprocessing", "command"),
        )

        logs = config.get("program:log-redirect-handler", "command")
        self.assertNotIn("celery_worker_monitoring.log", logs)
        self.assertNotIn("celery_worker_scheduled_tasks.log", logs)
        self.assertNotIn("slack_bot.log", logs)
        self.assertNotIn("discord_bot.log", logs)
        self.assertIn("mcp_server.log", logs)
        self.assertIn("celery_worker_primary.log", logs)

    def test_each_bot_opt_in_restores_exact_upstream_program_and_log(self) -> None:
        upstream = self.module.configparser.RawConfigParser(
            interpolation=None, strict=True
        )
        upstream.optionxform = str
        upstream.read(SUPERVISOR)
        for env_name in ("slack", "discord"):
            config = self._derive(slack=env_name == "slack", discord=env_name == "discord")
            section = f"program:{env_name}_bot"
            self.assertTrue(config.has_section(section))
            self.assertEqual(
                dict(config.items(section)), dict(upstream.items(section))
            )
            self.assertIn(
                f"/var/log/{env_name}_bot.log",
                config.get("program:log-redirect-handler", "command"),
            )

    def test_watchdog_is_local_file_only(self) -> None:
        config = self._derive()
        command = config.get("program:supervisord_watchdog_celery_beat", "command")
        self.assertIn("wrapper-beat-liveness-watchdog.py", command)
        self.assertTrue(command.startswith("python -S "))
        self.assertIn("/tmp/onyx_k8s_beat_liveness.txt", command)
        self.assertNotIn("redis", command.lower())
        source = WATCHDOG.read_text(encoding="utf-8")
        for forbidden in ("import onyx", "import redis", "import celery", "import sqlalchemy"):
            self.assertNotIn(forbidden, source.lower())

    def test_exact_control_processes_skip_background_sitecustomize(self) -> None:
        bootstrap = (
            ROOT / "onyx/patches/sitecustomize_background/sitecustomize.py"
        )
        for argv0 in (
            "/app/wrapper-background-entrypoint.py",
            "/app/wrapper-beat-liveness-watchdog.py",
            "/usr/bin/supervisord",
        ):
            with self.subTest(argv0=argv0), patch.object(sys, "argv", [argv0]):
                module = _load("control_audit", bootstrap)
                self.assertTrue(module._is_background_control_process())

    def test_compose_starts_background_entrypoint_without_sitecustomize(self) -> None:
        compose = (
            ROOT / "compose_overlays/docker-compose.full.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'entrypoint: ["python", "-S", "/app/wrapper-background-entrypoint.py"]',
            compose,
        )

    def test_bot_options_use_agent_names_in_section_five(self) -> None:
        example = (ROOT / ".env.wrapper.example").read_text(encoding="utf-8")
        section_five = example.index("# 5. Agent Web")
        section_six = example.index("# 6.")
        for name in ("ONYX_AGENT_SLACK_BOT", "ONYX_AGENT_DISCORD_BOT"):
            self.assertLess(section_five, example.index(f"{name}=false"))
            self.assertLess(example.index(f"{name}=false"), section_six)
        self.assertNotIn("ONYX_SLACK_BOT_ENABLED", example)
        self.assertNotIn("ONYX_DISCORD_BOT_ENABLED", example)


class BeatWatchdogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load("beat_watchdog_under_test", WATCHDOG)

    def test_constants_match_contract(self) -> None:
        self.assertEqual(self.module.CHECK_INTERVAL_SECONDS, 300)
        self.assertEqual(self.module.STALE_AFTER_SECONDS, 1200)
        self.assertEqual(self.module.STARTUP_GRACE_SECONDS, 1200)

    def test_liveness_path_rejects_missing_symlink_directory_owner_and_future(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing = root / "missing"
            self.assertIsNone(self.module._valid_mtime(missing, os.getuid(), 1000))
            directory = root / "dir"
            directory.mkdir()
            self.assertIsNone(self.module._valid_mtime(directory, os.getuid(), 1000))
            target = root / "target"
            target.write_text("", encoding="utf-8")
            symlink = root / "link"
            symlink.symlink_to(target)
            self.assertIsNone(self.module._valid_mtime(symlink, os.getuid(), 1000))
            os.utime(target, (2000, 2000))
            self.assertIsNone(self.module._valid_mtime(target, os.getuid(), 1000))
            os.utime(target, (900, 900))
            self.assertEqual(
                self.module._valid_mtime(target, os.getuid(), 1000), 900
            )

    def test_watch_restarts_after_two_missing_observations_outside_grace(self) -> None:
        run = MagicMock()
        self.module.watch(
            self.module.EXPECTED_PATH,
            "celery_beat",
            Path("/tmp/supervisord.conf"),
            sleep_fn=MagicMock(),
            monotonic_fn=MagicMock(side_effect=[0, 1201, 1501, 1501]),
            wall_time_fn=MagicMock(side_effect=[1201, 1501]),
            run_fn=run,
            max_checks=2,
        )
        run.assert_called_once_with(
            [
                "supervisorctl",
                "-c",
                "/tmp/supervisord.conf",
                "restart",
                "celery_beat",
            ],
            check=True,
        )

    def test_watch_restarts_stale_file_only_after_startup_grace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "liveness"
            path.write_text("", encoding="ascii")
            os.utime(path, (100, 100))
            with patch.object(self.module, "EXPECTED_PATH", path):
                run = MagicMock()
                self.module.watch(
                    path,
                    "celery_beat",
                    Path("/tmp/supervisord.conf"),
                    sleep_fn=MagicMock(),
                    monotonic_fn=MagicMock(side_effect=[0, 1200, 1201, 1201]),
                    wall_time_fn=MagicMock(side_effect=[2000, 2000]),
                    run_fn=run,
                    max_checks=2,
                )
            run.assert_called_once()

    def test_supervisor_failure_is_fatal(self) -> None:
        failed = subprocess.CalledProcessError(1, ["supervisorctl"])
        with self.assertRaises(subprocess.CalledProcessError):
            self.module.watch(
                self.module.EXPECTED_PATH,
                "celery_beat",
                Path("/tmp/supervisord.conf"),
                sleep_fn=MagicMock(),
                monotonic_fn=MagicMock(side_effect=[0, 1201, 1501]),
                wall_time_fn=MagicMock(side_effect=[1201, 1501]),
                run_fn=MagicMock(side_effect=failed),
                max_checks=2,
            )


if __name__ == "__main__":
    unittest.main()
