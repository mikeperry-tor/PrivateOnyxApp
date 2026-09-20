"""Canonical patch imports and fresh interpreter isolation for stateful tests."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


PATCH_ROOT = Path(__file__).resolve().parents[1] / "onyx/patches"
if str(PATCH_ROOT) not in sys.path:
    sys.path.insert(0, str(PATCH_ROOT))


def load_patch(owner: str, env: dict[str, str] | None = None):
    with patch.dict(os.environ, env or {}, clear=True):
        return importlib.import_module("onyx_wrapper_patches." + owner)


class FreshPatchTestCase(unittest.TestCase):
    """Each fixture gets one canonical module graph, never partial reloads."""

    def run(self, result=None):
        if os.environ.get("ONYX_PATCH_TEST_CASE") == self.id():
            return super().run(result)
        if result is None:
            result = self.defaultTestResult()
        result.startTest(self)
        env = dict(os.environ, ONYX_PATCH_TEST_CASE=self.id())
        env.pop("PYTHONPATH", None)
        completed = subprocess.run(
            [sys.executable, "-m", "unittest", self.id()],
            cwd=Path(__file__).resolve().parent,
            env=env, capture_output=True, text=True, timeout=120,
        )
        if completed.returncode:
            try:
                raise AssertionError(completed.stdout + completed.stderr)
            except AssertionError:
                result.addFailure(self, sys.exc_info())
        else:
            result.addSuccess(self)
        result.stopTest(self)
        return result
