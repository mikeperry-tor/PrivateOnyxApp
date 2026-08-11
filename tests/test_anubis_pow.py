from __future__ import annotations

import hashlib
import json
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "browser" / "obscura_client"))

from private_onyx_obscura import (  # noqa: E402
    AnubisChallenge,
    AnubisProtocolError,
    AnubisSolverError,
    parse_anubis_challenge,
    solve_anubis_fast,
)
from private_onyx_obscura.anubis import worker_preload_source  # noqa: E402


def _document(
    *,
    version="v1.25.0",
    algorithm="fast",
    difficulty=2,
    random_data=None,
    challenge_id="01980000-0000-7000-8000-000000000001",
):
    random_data = random_data or "ab" * 64
    payload = {
        "challenge": {
            "issuedAt": "2026-08-11T00:00:00Z",
            "metadata": {"User-Agent": "fixture"},
            "id": challenge_id,
            "method": algorithm,
            "randomData": random_data,
            "difficulty": difficulty,
            "spent": False,
        },
        "rules": {"algorithm": algorithm, "difficulty": difficulty},
    }
    return (
        '<script id="anubis_version" type="application/json">'
        + json.dumps(version)
        + "</script>"
        + '<script id="anubis_challenge" type="application/json">'
        + json.dumps(payload)
        + "</script>"
    )


class AnubisPowTests(unittest.TestCase):
    def test_parseable_fast_profile_is_not_release_or_work_factor_pinned(self):
        challenge = parse_anubis_challenge(_document())
        self.assertEqual(challenge.version, "v1.25.0")
        self.assertEqual(challenge.algorithm, "fast")
        self.assertEqual(challenge.difficulty, 2)
        self.assertEqual(len(challenge.random_data), 128)

        future = parse_anubis_challenge(
            _document(
                version="v9.8.7-custom",
                difficulty=12,
                random_data="FuturePuzzleSeed/+",
                challenge_id="future-opaque-challenge-token",
            ).replace('"rules":', '"extension": {"ignored": true}, "rules":')
        )
        self.assertEqual(future.version, "v9.8.7-custom")
        self.assertEqual(future.difficulty, 12)
        self.assertEqual(future.random_data, "FuturePuzzleSeed/+")
        self.assertEqual(future.challenge_id, "future-opaque-challenge-token")

    def test_unparseable_or_unsupported_puzzles_fail_closed(self):
        cases = (
            _document(version="bad\nversion"),
            _document(algorithm="slow"),
            _document(difficulty=65),
            _document(random_data="line\nbreak"),
            _document(random_data="non-ascii-é"),
            _document().replace("anubis_challenge", "other_challenge"),
            _document()
            + '<script id="anubis_challenge" type="application/json">{}</script>',
            _document().replace(
                '"spent": false',
                '"spent": true',
            ),
            _document().replace('"difficulty": 2, "spent"', '"difficulty": 3, "spent"'),
        )
        for markup in cases:
            with self.subTest(markup=markup[:80]):
                with self.assertRaises(AnubisProtocolError):
                    parse_anubis_challenge(markup)

    def test_solver_returns_the_first_valid_nonce_and_hash(self):
        challenge = parse_anubis_challenge(_document(difficulty=2))
        solution = solve_anubis_fast(
            challenge,
            deadline=time.monotonic() + 2.0,
        )
        expected = hashlib.sha256(
            (challenge.random_data + str(solution.nonce)).encode("ascii")
        ).hexdigest()
        self.assertEqual(solution.response, expected)
        self.assertTrue(expected.startswith("00"))
        for nonce in range(solution.nonce):
            prior = hashlib.sha256(
                (challenge.random_data + str(nonce)).encode("ascii")
            ).hexdigest()
            self.assertFalse(prior.startswith("00"))

    def test_solver_deadline_and_candidate_limit_are_strict(self):
        challenge = AnubisChallenge(
            "v1.25.0",
            "01980000-0000-7000-8000-000000000001",
            "cd" * 64,
            "fast",
            5,
        )
        clock_values = iter((0.0, 1.0))
        with self.assertRaises(AnubisSolverError):
            solve_anubis_fast(
                challenge,
                deadline=0.5,
                clock=lambda: next(clock_values),
            )
        if hashlib.sha256((challenge.random_data + "0").encode("ascii")).hexdigest().startswith("00000"):
            self.skipTest("fixture nonce unexpectedly satisfies difficulty")
        with self.assertRaises(AnubisSolverError):
            solve_anubis_fast(
                challenge,
                deadline=time.monotonic() + 1.0,
                max_candidates=1,
            )

    def test_worker_preload_is_limited_to_exact_anubis_worker_sources(self):
        source = worker_preload_source("__privateOnyxAnubis_" + "a" * 32)
        self.assertIn("sha256-(?:webcrypto|purejs)", source)
        self.assertIn("url.origin === location.origin", source)
        self.assertIn("decodeURIComponent(url.pathname) === mainPath", source)
        self.assertIn("url.protocol === 'blob:'", source)
        self.assertIn(
            "Reflect.construct(NativeWorker, [url, options], new.target)", source
        )
        self.assertNotIn("onmessage = () =>", source)
        with self.assertRaises(ValueError):
            worker_preload_source("broad-control")


if __name__ == "__main__":
    unittest.main()
