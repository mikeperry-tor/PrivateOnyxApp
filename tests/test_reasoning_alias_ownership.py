from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import patch

from patch_test_support import FreshPatchTestCase, load_patch


class ReasoningAliasTests(FreshPatchTestCase):
    def test_identity_repair_tolerates_inspection_failures_and_later_imports(self):
        reasoning = load_patch("api.reasoning")

        def original():
            return "original"

        def replacement():
            return "replacement"

        owner = ModuleType("reasoning_owner_fixture")
        consumer = ModuleType("reasoning_consumer_fixture")
        unrelated = ModuleType("reasoning_unrelated_fixture")
        broken = ModuleType("reasoning_broken_fixture")
        owner.detector = consumer.detector = original
        unrelated.detector = lambda: "unrelated"

        def inaccessible(name):
            raise RuntimeError("controlled attribute inspection failure")

        broken.__getattr__ = inaccessible
        fixtures = {module.__name__: module for module in (owner, consumer, unrelated, broken)}
        with patch.dict(sys.modules, fixtures):
            reasoning._update_bound_module_attr(original, replacement, "detector")
            self.assertIs(owner.detector, replacement)
            self.assertIs(consumer.detector, replacement)
            self.assertEqual(unrelated.detector(), "unrelated")
            later = {}
            exec("from reasoning_owner_fixture import detector", later)
            self.assertIs(later["detector"], replacement)
