from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

import validate_tor_image as validation


class TorImageVersionTests(unittest.TestCase):
    def test_expected_release_uses_selected_tag_with_digest_and_registry_port(self):
        for reference in (
            "docker.io/dockurr/tor:0.4.9.14",
            "docker.io/dockurr/tor:0.4.9.14@sha256:" + "a" * 64,
            "registry.example:5000/vendor/tor:0.4.9.14@sha256:" + "a" * 64,
        ):
            with self.subTest(reference=reference):
                self.assertEqual(validation.expected_tor_release(reference), "0.4.9.14")

    def test_missing_and_malformed_release_tags_are_rejected(self):
        for reference in ("tor", "tor:latest", "tor:0.4.9", "tor:0.4.9.14-dev",
                          "registry.example:5000/tor@sha256:" + "a" * 64):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                validation.expected_tor_release(reference)

    def test_actual_binary_release_matches_exactly(self):
        for output in ("Tor version 0.4.9.14.\nMore build metadata\n",
                       "Tor version 0.4.9.14 (git-metadata).\n"):
            validation.assert_tor_release(output, "0.4.9.14")
        for token in ("0.4.9.13.", "0.4.9.140.", "0.4.9.14.1.", "0.4.9.14-dev.", "0.4.9.14..", ""):
            with self.subTest(token=token), self.assertRaises(AssertionError):
                validation.assert_tor_release("Tor version " + token, "0.4.9.14")

    def test_invalid_selection_fails_before_engine_actions(self):
        arguments = ["validate_tor_image.py", "--engine", "docker", "--image", "fixture:wrapper",
                     "--base-image", "registry.example:5000/tor@sha256:" + "a" * 64]
        with patch.object(sys, "argv", arguments), patch.object(validation, "run") as run:
            with self.assertRaises(ValueError):
                validation.main()
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
