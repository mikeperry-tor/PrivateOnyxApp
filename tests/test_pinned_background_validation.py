from __future__ import annotations

import functools
from pathlib import Path
import tempfile
from types import ModuleType
import unittest

from validate_pinned_background import _validate_scheduler_tick


SOURCE = """class DynamicTenantScheduler:
    def tick(self):
        return super().tick()
"""


class SchedulerTickValidationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "beat.py"
        path.write_text(SOURCE)
        self.beat = ModuleType("beat_fixture")
        self.beat.__file__ = str(path)
        exec(compile(SOURCE, str(path), "exec", dont_inherit=True), vars(self.beat))

    def test_accepts_upstream_method_without_executing_source(self):
        with Path(self.beat.__file__).open("a") as source:
            source.write("raise AssertionError('must not execute upstream module')\n")
        _validate_scheduler_tick(self.beat)

    def test_rejects_method_replaced_before_validation(self):
        self.beat.DynamicTenantScheduler.tick = lambda self: 0
        with self.assertRaisesRegex(AssertionError, "tick was replaced"):
            _validate_scheduler_tick(self.beat)

    def test_rejects_wrapper_even_when_it_preserves_upstream_metadata(self):
        original = self.beat.DynamicTenantScheduler.tick

        @functools.wraps(original)
        def replacement(self):
            return 0

        self.beat.DynamicTenantScheduler.tick = replacement
        with self.assertRaisesRegex(AssertionError, "tick was replaced"):
            _validate_scheduler_tick(self.beat)
