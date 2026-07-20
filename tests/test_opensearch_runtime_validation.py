from __future__ import annotations

import json
import math
import unittest

from opensearch_runtime_validation import (
    ValidationError,
    _assert_no_failure_counter_increase,
    _bulk_documents,
    _heap_usage_summary,
    _vector,
)


class OpenSearchRuntimeValidationTests(unittest.TestCase):
    def test_vectors_are_normalized_and_nonzero(self) -> None:
        for number in (0, 1, 17, 999):
            vector = _vector(number)
            self.assertEqual(len(vector), 4)
            self.assertAlmostEqual(
                math.sqrt(sum(component * component for component in vector)),
                1.0,
            )

    def test_bulk_fixture_has_exact_actions_and_documents(self) -> None:
        lines = _bulk_documents("validation-index", 5, 3).splitlines()
        self.assertEqual(len(lines), 6)
        actions = [json.loads(line) for line in lines[0::2]]
        documents = [json.loads(line) for line in lines[1::2]]
        self.assertEqual(
            [action["index"]["_id"] for action in actions],
            ["doc-5", "doc-6", "doc-7"],
        )
        self.assertEqual(
            [document["document_id"] for document in documents],
            ["document-5", "document-6", "document-7"],
        )

    def test_failure_counters_must_not_increase(self) -> None:
        before = {"breaker_tripped": 2, "thread_pool_rejected": 3}
        _assert_no_failure_counter_increase(before, dict(before))
        with self.assertRaisesRegex(ValidationError, "breaker_tripped increased"):
            _assert_no_failure_counter_increase(
                before,
                {"breaker_tripped": 3, "thread_pool_rejected": 3},
            )

    def test_final_heap_sample_is_included_in_reported_maximum(self) -> None:
        self.assertEqual(
            _heap_usage_summary([100, 200, 150], 250),
            {
                "heap_used_max_sample_bytes": 250,
                "heap_used_final_bytes": 250,
            },
        )


if __name__ == "__main__":
    unittest.main()
