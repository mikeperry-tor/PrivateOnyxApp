from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ImmutableComponentPinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = (ROOT / "stack.versions.env").read_text(encoding="utf-8")
        cls.makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    def value(self, name: str) -> str:
        match = re.search(rf"^{re.escape(name)}=(.+)$", self.manifest, re.MULTILINE)
        self.assertIsNotNone(match, f"missing {name}")
        return match.group(1)

    def test_source_builds_require_exact_commits(self) -> None:
        for name in ("MYST_NODE_REF", "TEEP_REF", "MINIO_SOURCE_REF"):
            self.assertRegex(self.value(name), r"^[0-9a-f]{40}$")

        for path, argument in (
            ("myst/build/Dockerfile", "MYST_NODE_REF"),
            ("teep/build/Dockerfile", "TEEP_REF"),
        ):
            dockerfile = (ROOT / path).read_text(encoding="utf-8")
            self.assertIn(f"ARG {argument}", dockerfile)
            self.assertIn("git checkout --detach", dockerfile)
            self.assertIn("org.opencontainers.image.revision", dockerfile)
            self.assertNotRegex(dockerfile, rf"ARG {argument}=.+")

        self.assertIn(
            'MYST_NODE_REF="$(MYST_NODE_REF)"', self.makefile
        )
        self.assertIn('TEEP_REF="$(TEEP_REF)"', self.makefile)
        fallback = re.search(
            r"^TEEP_DEFAULT_REF := ([0-9a-f]{40})$",
            self.makefile,
            re.MULTILINE,
        )
        self.assertIsNotNone(fallback)
        self.assertEqual(fallback.group(1), self.value("TEEP_REF"))

    def test_mutable_support_tags_are_not_allowed(self) -> None:
        for name in ("TAILSCALE_IMAGE", "TOR_BASE_IMAGE"):
            image = self.value(name)
            self.assertRegex(image, r"@sha256:[0-9a-f]{64}$")
            self.assertNotIn(":latest", image)
            self.assertNotIn(":stable", image)
        self.assertNotIn("tailscale/tailscale:stable", self.makefile)
        self.assertNotIn("AUTOHEAL_IMAGE", self.manifest)
        self.assertNotIn("willfarrell/autoheal", self.manifest)

        executor_tag = self.value("PYTHON_EXECUTOR_IMAGE_TAG")
        self.assertRegex(executor_tag, r"^\d+\.\d+\.\d+$")
        self.assertNotEqual(executor_tag, "latest")
        self.assertEqual(executor_tag, self.value("CODE_INTERPRETER_IMAGE_TAG"))
        self.assertRegex(
            self.value("PYTHON_EXECUTOR_UPSTREAM_IMAGE"),
            r"^docker\.io/onyxdotapp/python-executor-sci:0\.4\.4@sha256:[0-9a-f]{64}$",
        )
        self.assertIn(
            "PYTHON_EXECUTOR_UPSTREAM_IMAGE ?= $(call env_value,PYTHON_EXECUTOR_UPSTREAM_IMAGE)",
            self.makefile,
        )
        self.assertIn("TOR_WRAPPER_SOURCE_HASH", self.makefile)
        self.assertIn("$(TOR_BASE_IMAGE)", self.makefile)

    def test_minio_release_records_its_image_source_revision(self) -> None:
        self.assertRegex(
            self.value("MINIO_IMAGE"),
            r"^docker\.io/minio/minio:RELEASE\.[0-9TZ-]+-cpuv1$",
        )
        self.assertRegex(self.value("MINIO_SOURCE_REF"), r"^[0-9a-f]{40}$")

    def test_external_images_use_explicit_registries(self) -> None:
        for name in (
            "PYTHON_EXECUTOR_UPSTREAM_IMAGE",
            "OBSCURA_IMAGE",
            "TOR_BASE_IMAGE",
            "NETNS_HOLDER_IMAGE",
            "PYTHON_SLIM_IMAGE",
            "PYTHON_ALPINE_IMAGE",
            "SOCAT_IMAGE",
            "TAILSCALE_IMAGE",
            "MINIO_IMAGE",
        ):
            self.assertTrue(
                self.value(name).startswith("docker.io/"),
                f"{name} must not rely on a short-name registry default",
            )

        for path in (
            "docker-compose.yaml",
            "compose_overlays/docker-compose.full.yml",
            "executor/Dockerfile",
            "myst/build/Dockerfile",
            "searxng/Dockerfile",
            "teep/build/Dockerfile",
        ):
            contents = (ROOT / path).read_text(encoding="utf-8")
            self.assertNotRegex(
                contents,
                r"(?m)^\s*(?:image:|FROM|ARG "
                r"PYTHON_EXECUTOR_UPSTREAM_IMAGE=)\s*"
                r"(?!docker\.io/|local/|\$\{)[a-z0-9][a-z0-9_.-]*/",
                path,
            )
            for line in contents.splitlines():
                if not line.startswith("FROM "):
                    continue
                fields = [
                    field
                    for field in line.removeprefix("FROM ").split()
                    if not field.startswith("--")
                ]
                self.assertTrue(fields, path)
                self.assertTrue(
                    fields[0].startswith(("docker.io/", "${")),
                    f"{path} has an unqualified FROM reference: {fields[0]}",
                )


if __name__ == "__main__":
    unittest.main()
