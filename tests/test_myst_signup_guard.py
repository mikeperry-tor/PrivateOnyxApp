from __future__ import annotations

import unittest

from myst import signup_guard


class MystSignupGuardTests(unittest.TestCase):
    def test_absent_container_is_allowed(self) -> None:
        signup_guard.validate_existing(None, allowed_project="signup", engine="docker")

    def test_required_absent_container_is_rejected(self) -> None:
        with self.assertRaisesRegex(signup_guard.GuardError, "no docker signup"):
            signup_guard.validate_existing(
                None,
                allowed_project="signup",
                engine="docker",
                require_existing=True,
            )

    def test_exact_setup_project_is_allowed(self) -> None:
        signup_guard.validate_existing(
            {
                "Config": {
                    "Labels": {"com.docker.compose.project": "signup"},
                    "Env": ["MYST_SETUP_ONLY=true"],
                }
            },
            allowed_project="signup",
            engine="docker",
        )

    def test_integrated_or_wrong_mode_container_is_rejected(self) -> None:
        for project, environment in (
            ("onyx", ["MYST_SETUP_ONLY=false"]),
            ("signup", ["MYST_SETUP_ONLY=false"]),
            ("other", ["MYST_SETUP_ONLY=true"]),
        ):
            with self.assertRaisesRegex(signup_guard.GuardError, "refusing"):
                signup_guard.validate_existing(
                    {"Config": {"Labels": {"com.docker.compose.project": project}, "Env": environment}},
                    allowed_project="signup",
                    engine="docker",
                )

    def test_classifier_distinguishes_absent_setup_and_integrated(self) -> None:
        self.assertEqual(
            signup_guard.classify_existing(
                None, allowed_project="signup", engine="podman"
            ),
            "absent",
        )
        for project, setup_only, expected in (
            ("signup", "true", "setup"),
            ("onyx", "false", "integrated"),
        ):
            self.assertEqual(
                signup_guard.classify_existing(
                    {
                        "Config": {
                            "Labels": {"com.docker.compose.project": project},
                            "Env": [f"MYST_SETUP_ONLY={setup_only}"],
                        }
                    },
                    allowed_project="signup",
                    engine="podman",
                ),
                expected,
            )


if __name__ == "__main__":
    unittest.main()
