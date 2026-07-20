from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

from tests import health_inventory


class HealthInventoryTests(unittest.TestCase):
    @patch.object(health_inventory.subprocess, "run")
    def test_uses_selected_engine_env_files_and_active_profiles(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps(
                {
                    "services": {
                        "core": {
                            "healthcheck": {
                                "test": ["CMD", "true"],
                                "interval": "10m0s",
                            }
                        },
                        "enabled-profile": {
                            "profiles": ["tailscale"],
                            "healthcheck": {
                                "test": ["CMD", "true"],
                                "interval": "1m0s",
                            },
                        },
                        "disabled-profile": {
                            "profiles": ["other"],
                            "healthcheck": {
                                "test": ["CMD", "true"],
                                "interval": "1m0s",
                            },
                        },
                    }
                }
            ),
        )
        with patch.dict(
            health_inventory.os.environ, {"COMPOSE_PROFILES": "tailscale"}
        ):
            rows = health_inventory.inventory(
                "full", "podman", ["stack.versions.env", ".env.wrapper"]
            )
        self.assertEqual(
            run.call_args.args[0],
            [
                "podman",
                "compose",
                "--env-file",
                "stack.versions.env",
                "--env-file",
                ".env.wrapper",
                "config",
                "--format",
                "json",
            ],
        )
        self.assertEqual(
            [row["service"] for row in rows], ["core", "enabled-profile"]
        )
        self.assertEqual(sum(float(row["checks_per_hour"]) for row in rows), 66.0)


if __name__ == "__main__":
    unittest.main()
