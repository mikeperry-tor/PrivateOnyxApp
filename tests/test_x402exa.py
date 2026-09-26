from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from searxng.engines import _x402_payment as payment
from searxng.patches.x402_config import enabled
from searxng import wallet

KEY = "0" * 63 + "1"  # Public, unfunded deterministic fixture. Never operator data.


def encoded(value):
    return base64.b64encode(json.dumps(value).encode()).decode()


def challenge(**updates):
    offer = {"scheme": "exact", "network": payment.NETWORK, "asset": payment.ASSET,
             "amount": "2000000", "payTo": "0x" + "2" * 40, "maxTimeoutSeconds": 60,
             "extra": {"name": "USD Coin", "version": "2"}}
    offer.update(updates)
    return {"x402Version": 2, "resource": {"url": payment.ENDPOINT}, "accepts": [offer]}


class ConfigurationTests(unittest.TestCase):
    def test_configuration_is_explicit_and_secret_safe(self):
        for value in (None, ""):
            self.assertFalse(enabled(value, "false"))
        for value in (" ", "secret-canary", "0" * 64, "f" * 64):
            with self.assertRaises(ValueError) as error:
                enabled(value)
            if value.strip():
                self.assertNotIn(value, str(error.exception))
        self.assertTrue(enabled(KEY))
        self.assertTrue(enabled("0x" + KEY))
        with self.assertRaises(ValueError):
            enabled(KEY, "false")

    def test_request_product_and_time_bounds(self):
        body = json.loads(payment.request_body("site:example.org a", {"safesearch": 2, "time_range": "day"}))
        self.assertEqual(body["query"], "site:example.org a")
        self.assertEqual(body["contents"], {"text": False, "highlights": True})
        self.assertEqual(body["type"], "auto")
        self.assertTrue(body["moderation"])
        self.assertIn("startPublishedDate", body)
        self.assertEqual(payment.retry_after("99999"), 3600)
        self.assertEqual(payment.retry_after("nonsense"), 60)

    def test_result_validation_preserves_order_and_identity(self):
        def normalize(value, **kwargs):
            if not value.startswith("https://"):
                raise ValueError()
            url, _, fragment = value.partition("#")
            return url, fragment
        good = {"url": "https://example.org/item?id=1%2F2#x%2Fy", "title": "Title", "highlights": ["a" * 2500]}
        results = payment.parse_results({"results": [{}, good]}, str, normalize)
        self.assertEqual(results[0]["url"], good["url"])
        self.assertEqual(len(results[0]["content"]), 2000)
        with self.assertRaises(ValueError):
            payment.parse_results({"results": [{}]}, str, normalize)
        self.assertEqual(payment.parse_results({"results": []}, str, normalize), [])


@unittest.skipUnless(os.name == "posix", "wallet permissions require the supported POSIX hosts")
class WalletTests(unittest.TestCase):
    def test_concurrent_creation_and_symlink_rejection(self):
        from types import SimpleNamespace
        payload = json.dumps({"key": "0x" + KEY, "address": "0x" + "a" * 40}).encode()
        generated = []
        def run(command, **kwargs):
            if "run" in command:
                generated.append(True)
            return SimpleNamespace(returncode=0, stdout=payload)
        with tempfile.TemporaryDirectory() as root, patch.object(wallet.subprocess, "run", side_effect=run):
            directory = Path(root).resolve() / "wallet"
            successes = []
            failures = []
            def create():
                try:
                    successes.append(wallet.create_wallet("docker", "local:test", directory))
                except FileExistsError:
                    failures.append(True)
            threads = [threading.Thread(target=create) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual((len(successes), len(failures), len(generated)), (1, 7, 1))
            alias = directory.parent / "alias"
            alias.symlink_to(directory, target_is_directory=True)
            with self.assertRaises(OSError):
                wallet.create_wallet("docker", "local:test", alias)

    def test_exclusive_permissions_no_inheritance_and_cleanup(self):
        from types import SimpleNamespace
        payload = json.dumps({"key": "0x" + KEY, "address": "0x" + "a" * 40}).encode()
        calls = []
        def run(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(returncode=0, stdout=payload)
        with tempfile.TemporaryDirectory() as root, patch.object(wallet.subprocess, "run", side_effect=run):
            directory = Path(root).resolve() / "wallet"
            with patch.dict(os.environ, {"SEARXNG_X402_PRIVKEY": "canary"}):
                address, path = wallet.create_wallet("docker", "local:test", directory)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.read_text(), "SEARXNG_X402_PRIVKEY=0x" + KEY + "\n")
            self.assertNotIn("SEARXNG_X402_PRIVKEY", calls[-1][1]["env"])
            self.assertIn("none", calls[-1][0])
            self.assertIn("--log-driver", calls[-1][0])
            with self.assertRaises(FileExistsError):
                wallet.create_wallet("docker", "local:test", directory)
            path.unlink()
            with patch.object(wallet.subprocess, "run", side_effect=[SimpleNamespace(returncode=0), KeyboardInterrupt()]):
                with self.assertRaises(KeyboardInterrupt):
                    wallet.create_wallet("docker", "local:test", directory)
            self.assertFalse(path.exists())
            directory.chmod(0o755)
            with self.assertRaises(ValueError):
                wallet.create_wallet("docker", "local:test", directory)


class AdmissionAndRunnerTests(unittest.TestCase):
    def test_atomic_api_ownership_spacing_and_suspension_before_release(self):
        from searxng.engines import _x402_admission as admission
        tokens = []
        with patch.object(admission, "_last_start", float("-inf")):
            threads = [threading.Thread(target=lambda: tokens.append(admission.reserve(lambda: True))) for _ in range(12)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            owned = [token for token in tokens if token]
            self.assertEqual(len(owned), 1)
            suspended = False
            with admission.ownership(owned[0]) as start:
                start()
                self.assertIsNone(admission.reserve(lambda: True))
                suspended = True  # Native processor records outcome before release.
            self.assertIsNone(admission.wait(lambda: not suspended))
            self.assertIsNone(admission.reserve(lambda: True))
            with admission._condition:
                admission._last_start -= 3
            token = admission.reserve(lambda: True)
            self.assertIsNotNone(token)
            admission.release(token)

    def test_live_runner_submits_one_search_and_never_replays_failure(self):
        import io
        from contextlib import redirect_stdout
        from types import SimpleNamespace
        from tests import integration_x402exa as runner
        good = {"results": [{"engines": ["x402exa"], "content": "snippet", "url": "https://example.org"}]}
        for result in (good, {"results": []}, TimeoutError("ambiguous")):
            calls = []
            def open_request(request, **kwargs):
                calls.append(request)
                if len(calls) == 1:
                    return io.BytesIO(json.dumps({"engines": [{"name": "x402exa"}]}).encode())
                self.assertEqual(len(calls), 2)
                if isinstance(result, Exception):
                    raise result
                return io.BytesIO(json.dumps(result).encode())
            with patch.object(runner, "build_opener", return_value=SimpleNamespace(open=open_request)), redirect_stdout(io.StringIO()):
                if result is good:
                    runner.run()
                else:
                    with self.assertRaises(Exception):
                        runner.run()
            self.assertEqual(len(calls), 2)
            self.assertIn(b"engines=x402exa", calls[1].data)


@unittest.skipUnless(importlib.util.find_spec("x402"), "requires selected SearXNG image SDK")
class SDKTests(unittest.TestCase):
    def setUp(self):
        self.client = payment.PaymentClient(KEY)

    def test_sdk_offer_policy_uncapped_signing_nonce_and_lifetime(self):
        from x402.http.utils import decode_payment_signature_header
        self.client.requirements = self.client.decode(encoded(challenge(maxTimeoutSeconds=0)))
        before = int(time.time())
        first = decode_payment_signature_header(self.client.sign())
        second = decode_payment_signature_header(self.client.sign())
        auth = first.payload["authorization"]
        self.assertEqual(auth["value"], "2000000")  # Above SDK's default $1 ceiling.
        self.assertEqual(auth["validAfter"], "0")
        self.assertGreaterEqual(int(auth["validBefore"]), before + 3600)
        self.assertNotEqual(auth["nonce"], second.payload["authorization"]["nonce"])
        for extra in ({"assetTransferMethod": "permit2"}, {"assetTransferMethod": "unknown"},
                      {"paymentFlow": "escrow"}, {"paymentFlow": "upfront"},
                      {"name": "USD Coin"}, {"name": "bad", "version": "2"}):
            with self.subTest(extra=extra), self.assertRaises(Exception):
                self.client.decode(encoded(challenge(extra=extra)))
        self.client.decode(encoded(challenge(extra={})))
        for field, value in (("x402Version", 3), ("extensions", {"unknown": {}}),
                             ("resource", {"url": "https://other.example/search"})):
            with self.assertRaises(Exception):
                self.client.decode(encoded(challenge() | {field: value}))
        omitted = challenge()
        del omitted["x402Version"]
        self.assertEqual(self.client.decode(encoded(omitted)).x402_version, 2)

    def test_discovery_cache_fresh_authorization_and_no_repayment(self):
        import httpx
        calls = []
        receipt = encoded({"success": True, "transaction": "server-assertion", "network": payment.NETWORK})
        def handler(request):
            calls.append(request)
            if "PAYMENT-SIGNATURE" not in request.headers:
                return httpx.Response(402, headers={"PAYMENT-REQUIRED": encoded(challenge())})
            return httpx.Response(200, headers={"PAYMENT-RESPONSE": receipt}, json={"results": []})
        for body in (b'{"query":"first"}', b'{"query":"second"}'):
            outcome = payment.Outcome()
            result = asyncio.run(payment.exchange(self.client, body, time.monotonic() + 5,
                outcome, lambda: None, transport=httpx.MockTransport(handler)))
            self.assertEqual(result, {"results": []})
            self.assertEqual(outcome.payment, "reported_success")
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0].content, calls[1].content)
        self.assertNotEqual(calls[1].headers["PAYMENT-SIGNATURE"], calls[2].headers["PAYMENT-SIGNATURE"])
        rejected = []
        def reject(request):
            rejected.append(request)
            return httpx.Response(402, headers={"PAYMENT-REQUIRED": encoded(challenge(amount="3")),
                "PAYMENT-RESPONSE": encoded({"success": False, "transaction": "", "network": payment.NETWORK})})
        outcome = payment.Outcome()
        with self.assertRaises(ValueError):
            asyncio.run(payment.exchange(self.client, b"{}", time.monotonic()+5, outcome, lambda:None,
                        transport=httpx.MockTransport(reject)))
        self.assertEqual(len(rejected), 1)
        self.assertEqual(outcome.payment, "reported_rejection")
        self.assertEqual(self.client.requirements.accepts[0].amount, "3")

    def test_receipt_survives_body_failure_and_unsigned_200_never_signs(self):
        import httpx
        class Broken(httpx.AsyncByteStream):
            async def __aiter__(self):
                raise httpx.RemoteProtocolError("secret-provider-body")
                yield b""
        receipt = encoded({"success": True, "transaction": "", "network": payment.NETWORK})
        self.client.requirements = self.client.decode(encoded(challenge()))
        outcome = payment.Outcome()
        with self.assertRaises(httpx.RemoteProtocolError):
            asyncio.run(payment.exchange(self.client, b"{}", time.monotonic()+5, outcome, lambda:None,
                transport=httpx.MockTransport(lambda req:httpx.Response(200, headers={"PAYMENT-RESPONSE":receipt}, stream=Broken()))))
        self.assertEqual(outcome.payment, "reported_success")
        self.client.requirements = None
        outcome = payment.Outcome()
        with patch.object(self.client, "sign", side_effect=AssertionError("must not sign")):
            asyncio.run(payment.exchange(self.client, b"{}", time.monotonic()+5, outcome, lambda:None,
                transport=httpx.MockTransport(lambda req:httpx.Response(200, json={"results":[]}))))
        self.assertFalse(outcome.submitted)
        self.assertIsNone(self.client.requirements)

    def test_expired_signing_cannot_dispatch(self):
        import httpx
        self.client.requirements = self.client.decode(encoded(challenge()))
        original = self.client.sign
        def sign():
            result = original()
            time.sleep(.03)
            return result
        outcome = payment.Outcome()
        with patch.object(self.client, "sign", side_effect=sign), self.assertRaises(TimeoutError):
            asyncio.run(payment.exchange(self.client, b"{}", time.monotonic()+.01, outcome, lambda:None,
                transport=httpx.MockTransport(lambda req: self.fail("late dispatch"))))
        self.assertFalse(outcome.submitted)

    def test_sdk_mixed_offers_coercions_and_fresh_timestamps_after_expiry(self):
        from x402.http.utils import decode_payment_signature_header
        first = challenge()["accepts"][0]
        value = challenge()
        value["x402Version"] = "2"
        value["accepts"] = [first | {"network": "solana:unsupported"}, first | {"maxTimeoutSeconds": "60"}, first | {"amount": "3"}]
        self.client.requirements = self.client.decode(encoded(value))
        for now in (1000, 100000):
            with patch("x402.mechanisms.evm.exact.client.time.time", return_value=now):
                signed = decode_payment_signature_header(self.client.sign())
            self.assertEqual(signed.accepted.amount, "2000000")
            self.assertEqual(signed.payload["authorization"]["validBefore"], str(now+60))
        self.assertIsNotNone(self.client.requirements)

    def test_429_and_ambiguous_failure_preserve_terms_without_rediscovery(self):
        import httpx
        self.client.requirements = self.client.decode(encoded(challenge()))
        cached = self.client.requirements
        for status in (429, 403, 500):
            calls = []
            outcome = payment.Outcome()
            def handler(request):
                calls.append(request)
                return httpx.Response(status, headers={"Retry-After": "99999"})
            with self.assertRaises(Exception):
                asyncio.run(payment.exchange(self.client, b"{}", time.monotonic()+5, outcome, lambda:None,
                            transport=httpx.MockTransport(handler)))
            self.assertEqual(len(calls), 1)
            self.assertIn("PAYMENT-SIGNATURE", calls[0].headers)
            self.assertIs(self.client.requirements, cached)
            self.assertEqual(outcome.payment, "unknown")
            if status in (429, 403):
                self.assertEqual(outcome.suspension, 3600)
        self.client.requirements = None
        calls = []
        outcome = payment.Outcome()
        with patch.object(self.client, "sign", side_effect=AssertionError("cold 429 signed")), self.assertRaises(ValueError):
            asyncio.run(payment.exchange(self.client, b"{}", time.monotonic()+5, outcome, lambda:None,
                transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(429))))
        self.assertEqual(len(calls), 1)
        self.assertFalse(outcome.submitted)

    def test_legacy_receipt_and_redirect_are_terminal(self):
        import httpx
        receipt = encoded({"success": True, "transaction": "", "network": payment.NETWORK})
        for status, headers in ((200, {"X-PAYMENT-RESPONSE": receipt}),
                                (302, {"Location": "https://other.example", "PAYMENT-RESPONSE": receipt})):
            self.client.requirements = self.client.decode(encoded(challenge()))
            calls = []
            with self.assertRaises(Exception):
                asyncio.run(payment.exchange(self.client, b"{}", time.monotonic()+5, payment.Outcome(), lambda:None,
                    transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(status, headers=headers))))
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
