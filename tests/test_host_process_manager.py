from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import host_process_manager as manager


class HostProcessManagerTests(unittest.TestCase):
    def test_record_round_trip_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "service.pid"
            record = manager.Record(123, "a" * 64, "config")
            manager._write_record(path, record)
            self.assertEqual(manager._read_record(path), record)
            self.assertEqual(list(path.parent.glob(".*.tmp")), [])

    def test_malformed_record_is_not_treated_as_unowned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = root / "service.pid"
            record.write_text("123\nshort\nconfig\n", encoding="ascii")
            args = argparse.Namespace(
                command=["true"],
                fingerprint_file=[],
                record_file=record,
                identity="/service.py",
                name="test service",
                port=12345,
            )
            with self.assertRaisesRegex(manager.ContractError, "malformed"):
                manager.start(args)

    @patch.object(manager, "_tcp_ready", return_value=True)
    def test_untracked_listener_is_accepted_only_when_requested(self, _ready) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = argparse.Namespace(
                command=["/missing"],
                fingerprint_file=[],
                record_file=root / "service.pid",
                identity="/service.py",
                name="operator service",
                port=12345,
                allow_untracked_listener=True,
            )
            manager.start(args)

    @patch.object(manager, "_ready", return_value=False)
    @patch.object(manager, "_owned", return_value=True)
    def test_owned_matching_process_must_also_be_ready(self, _owned, _ready) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record_path = root / "service.pid"
            command = ["python3", "/service.py"]
            manager._write_record(
                record_path,
                manager.Record(123, "b" * 64, manager._config_id(command, [])),
            )
            args = argparse.Namespace(
                command=command,
                fingerprint_file=[],
                record_file=record_path,
                identity="/service.py",
                name="test service",
                port=12345,
                health_path=None,
            )
            with self.assertRaisesRegex(manager.ContractError, "running but not ready"):
                manager.start(args)

    @patch.object(manager, "_owned", return_value=False)
    def test_stop_never_signals_a_reused_pid(self, _owned) -> None:
        with tempfile.TemporaryDirectory() as directory:
            record_path = Path(directory) / "service.pid"
            manager._write_record(
                record_path, manager.Record(456, "c" * 64, "config")
            )
            args = argparse.Namespace(
                record_file=record_path,
                identity="/service.py",
                name="test service",
                stop_timeout=1,
            )
            with patch.object(manager.os, "kill") as kill:
                manager.stop(args)
            kill.assert_not_called()
            self.assertFalse(record_path.exists())


if __name__ == "__main__":
    unittest.main()
