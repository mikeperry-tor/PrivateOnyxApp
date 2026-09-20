from __future__ import annotations

import copy
from datetime import timedelta
from types import SimpleNamespace
import unittest

from patch_test_support import load_patch


class MaterializedScheduleTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_patch("background.resource_policy")
        identifiers = dict(self.policy._DISCOVERY_TASKS)
        identifiers.update({name: value[0] for name, value in self.policy._HOUSEKEEPING_TASKS.items()})
        self.ids = SimpleNamespace(**{identifier: name + "-task" for name, identifier in identifiers.items()})
        self.tasks = [
            {"name": name, "task": name + "-task", "schedule": timedelta(minutes=5), "options": {}}
            for name in self.policy._DISCOVERY_TASKS
        ] + [
            {"name": name, "task": name + "-task", "schedule": cadence, "options": {}}
            for name, (_, cadence) in self.policy._HOUSEKEEPING_TASKS.items()
        ]

    def validate(self, tasks):
        self.policy.validate_materialized_schedule(tasks, self.ids, "monitoring")

    def test_exact_retained_producers(self):
        self.validate(self.tasks)

    def test_duplicate_housekeeping_cannot_hide_in_mapping(self):
        with self.assertRaisesRegex(RuntimeError, "duplicate"):
            self.validate(self.tasks + [copy.deepcopy(self.tasks[-1])])

    def test_unexpected_removed_missing_and_wrong_fields(self):
        for mutation in ("name", "task", "schedule", "queue", "missing"):
            with self.subTest(mutation=mutation):
                tasks = copy.deepcopy(self.tasks)
                if mutation == "name":
                    tasks[0]["name"] = "monitor-celery-queues"
                elif mutation == "queue":
                    tasks[0]["options"]["queue"] = "monitoring"
                elif mutation == "missing":
                    tasks.pop()
                else:
                    tasks[0][mutation] = "wrong"
                with self.assertRaises(RuntimeError):
                    self.validate(tasks)
