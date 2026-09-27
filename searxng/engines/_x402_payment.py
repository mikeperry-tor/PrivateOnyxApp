"""Inert Exa payment primitive. The engine processor owns admission and errors."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

ENDPOINT = "https://api.exa.ai/search"
PROXY = "http://searxng-x402-egress-bridge:3128"
NETWORK = "eip155:8453"
ASSET = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


@dataclass
class Outcome:
    submitted: bool = False
    payment: str = "not_submitted"
    reason: str = "provider_unavailable"
    suspension: int | None = None


def supported_offer(offer):
    extra = offer.extra or {}
    return (
        offer.scheme == "exact"
        and offer.network == NETWORK
        and offer.asset.lower() == ASSET.lower()
        and extra.get("assetTransferMethod") in (None, "eip3009")
        and extra.get("paymentFlow") in (None, "authorization")
        and ("name" not in extra or (
            extra["name"] == "USD Coin" and extra.get("version", "1") == "2"
        ))
    )


class PaymentClient:
    """One worker-local requirements entry; never retains signed payments."""

    def __init__(self, key):
        from eth_account import Account
        from x402 import x402ClientSync
        from x402.mechanisms.evm.exact.client import ExactEvmScheme
        from x402.mechanisms.evm.signers import EthAccountSigner

        logging.getLogger("x402.signers").setLevel(logging.WARNING)
        self.client = x402ClientSync()
        self.client.register(NETWORK, ExactEvmScheme(EthAccountSigner(Account.from_key(key))))
        self.client.set_spend_controls({"max_amount_per_payment": False})
        self.client.register_policy(lambda version, offers: [
            offer for offer in offers if version == 2 and supported_offer(offer)
        ])
        self.requirements = None

    def decode(self, header):
        from x402.http.utils import decode_payment_required_header

        requirements = decode_payment_required_header(header)
        if (requirements.x402_version != 2
                or (requirements.resource and requirements.resource.url != ENDPOINT)):
            raise ValueError("unsupported_payment_policy")
        # Optional extensions are not part of this client's payment contract.
        # Never pass them to SDK hooks/schemes or echo them into signatures.
        requirements = requirements.model_copy(update={"extensions": None})
        # SDK selection validates deployment policy without creating a payment.
        self.client._select_requirements_v2(requirements.accepts)
        return requirements

    def rejected(self, header):
        self.requirements = None
        if header:
            try:
                self.requirements = self.decode(header)
            except Exception:
                # A rejected attempt is already terminal. Invalid replacement
                # terms stay absent; the next attempt must discover again.
                self.requirements = None

    def sign(self):
        from x402.http.utils import encode_payment_signature_header

        logging.getLogger("x402.signers").setLevel(logging.WARNING)
        return encode_payment_signature_header(
            self.client.create_payment_payload(self.requirements)
        )


def check_deadline(deadline):
    if time.monotonic() >= deadline or getattr(threading.current_thread(), "_timeout", False):
        raise TimeoutError("engine_deadline")


def request_body(query):
    body = {
        "query": query, "type": "auto", "numResults": 10,
        "contents": {"text": False, "highlights": True},
        "moderation": False,
    }
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def retry_after(value):
    try:
        delay = int(value)
    except (ValueError, TypeError):
        try:
            delay = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            delay = 60
    return max(60, min(3600, delay))


def classify_status(response, outcome):
    if response.status_code == 429:
        outcome.reason = "rate_limited"
        outcome.suspension = retry_after(response.headers.get("retry-after"))
    elif response.status_code in (401, 403):
        outcome.reason = "provider_unavailable"
        outcome.suspension = 3600
    elif response.status_code == 402 and outcome.submitted:
        outcome.reason = "payment_rejected"


async def exchange(payment, body, deadline, outcome, record_start, *, transport=None):
    """At most one unsigned POST and one signed POST; no recovery/replay."""
    import httpx
    from x402.http.utils import decode_payment_response_header

    # httpcore DEBUG includes raw response headers, including payment data.
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    check_deadline(deadline)
    # No environment proxies, netrc, redirects, retries, or TLS bypass.
    transport = transport or httpx.AsyncHTTPTransport(
        proxy=PROXY, verify=True, retries=0, trust_env=False,
    )
    async with asyncio.timeout(max(0, deadline - time.monotonic())):
        async with httpx.AsyncClient(transport=transport, trust_env=False,
                                     follow_redirects=False, timeout=None) as client:
            headers = {"Content-Type": "application/json"}
            cold = payment.requirements is None
            if payment.requirements is None:
                check_deadline(deadline)
                record_start()
                async with client.stream("POST", ENDPOINT, content=body, headers=headers) as response:
                    classify_status(response, outcome)
                    if response.status_code == 200:
                        outcome.reason = "invalid_response"
                        await response.aread()
                        check_deadline(deadline)
                        result = response.json()
                        check_deadline(deadline)
                        return result
                    if response.status_code != 402:
                        raise ValueError("discovery_failed")
                    outcome.reason = "invalid_response"
                    check_deadline(deadline)
                    payment.requirements = payment.decode(response.headers.get("PAYMENT-REQUIRED"))
                    check_deadline(deadline)
            check_deadline(deadline)
            headers["PAYMENT-SIGNATURE"] = payment.sign()
            check_deadline(deadline)
            if not cold:
                record_start()
            check_deadline(deadline)
            outcome.submitted = True
            outcome.payment = "unknown"
            outcome.reason = "payment_status_unknown"
            async with client.stream("POST", ENDPOINT, content=body, headers=headers) as response:
                classify_status(response, outcome)
                receipt = None
                try:
                    receipt = decode_payment_response_header(response.headers.get("PAYMENT-RESPONSE"))
                except Exception:
                    # Cache rejection is independent of receipt validity.
                    if response.status_code == 402:
                        payment.rejected(response.headers.get("PAYMENT-REQUIRED"))
                    raise
                outcome.payment = "reported_success" if receipt.success else "reported_rejection"
                if response.status_code == 402 or not receipt.success:
                    payment.rejected(response.headers.get("PAYMENT-REQUIRED"))
                    if response.status_code not in (401, 403, 429):
                        outcome.reason = "payment_rejected"
                    raise ValueError("payment_rejected")
                if not response.is_success:
                    raise ValueError("paid_response_failed")
                outcome.reason = "invalid_response"
                await response.aread()
                check_deadline(deadline)
                result = response.json()
                check_deadline(deadline)
                return result


def parse_results(data, plain_text, normalize_url):
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ValueError("invalid_response")
    results = []
    invalid = 0
    for row in data["results"]:
        try:
            if not isinstance(row, dict) or not isinstance(row.get("title"), str):
                raise ValueError("invalid_row")
            highlights = row.get("highlights")
            if not isinstance(highlights, list) or not highlights or not all(isinstance(x, str) for x in highlights):
                raise ValueError("invalid_row")
            title = plain_text(row["title"]).strip()
            snippet = " ".join(plain_text(x).strip() for x in highlights).strip()[:2000]
            if not title or not snippet:
                raise ValueError("invalid_row")
            url, fragment = normalize_url(row.get("url"), allow_http=True)
            results.append({"url": url + ("#" + fragment if fragment else ""),
                            "title": title, "content": snippet})
        except (ValueError, TypeError, AttributeError):
            invalid += 1
        if len(results) == 10:
            break
    if invalid:
        logging.getLogger("searx.engines.x402exa").warning("invalid_rows=%d", invalid)
    if invalid and not results:
        raise ValueError("invalid_response")
    return results
