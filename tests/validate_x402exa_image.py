"""Real pinned scheduler/processor/engine with synthetic payments, offline only."""
import asyncio
import base64
import concurrent.futures
import json
import os
import threading
import time
from unittest.mock import patch

import httpx
import searx
import searx.search
from searx.search.models import EngineRef, SearchQuery
from searx.engines import _x402_payment, _x402_admission, _obscura
from flask import Flask
from x402_bootstrap import payment_client


def encode(value):
    return base64.b64encode(json.dumps(value).encode()).decode()


searx.search.initialize()
assert set(searx.search.PROCESSORS) == {"google2", "brave2", "duckduckgo2", "startpage2", "bing2", "x402exa"}
assert "x402exa" not in _obscura._PROVIDERS
assert all("privkey" not in json.dumps(e).lower() for e in searx.settings["engines"])
calls = []
attempts = []
terms = {"x402Version": 2, "resource": {"url": _x402_payment.ENDPOINT},
"extensions": {"bazaar": {"info": {}, "schema": {}},
               "agentkit": {"info": {"nonce": "never-sign"}, "schema": {}}}, "accepts": [{
    "scheme": "exact", "network": _x402_payment.NETWORK, "asset": _x402_payment.ASSET,
    "amount": "7000", "payTo": "0x" + "2" * 40, "maxTimeoutSeconds": 60,
    "extra": {"name": "USD Coin", "version": "2"},
}]}


def respond(request):
    calls.append(request)
    if "PAYMENT-SIGNATURE" not in request.headers:
        return httpx.Response(402, headers={"PAYMENT-REQUIRED": encode(terms)})
    from x402.http.utils import decode_payment_signature_header
    assert not decode_payment_signature_header(request.headers["PAYMENT-SIGNATURE"]).extensions
    return httpx.Response(200, headers={"PAYMENT-RESPONSE": encode({
        "success": True, "transaction": "synthetic", "network": _x402_payment.NETWORK,
    })}, json={"results": [{"url": "https://example.org/item?id=1%2F2#x%2Fy", "title": "Example", "highlights": ["A useful snippet"]}]})


exchange = _x402_payment.exchange
async def fake_exchange(*args, **kwargs):
    attempts.append("x402exa")
    return await exchange(*args, **kwargs, transport=httpx.MockTransport(respond))


app = Flask(__name__)
with patch.object(_x402_payment, "exchange", fake_exchange):
    # Use the real browser leases and native processors, replacing only provider
    # I/O. No production configuration can install these free-provider fixtures.
    for name, processor in searx.search.PROCESSORS.items():
        if name != "x402exa":
            processor.engine.search = lambda q, p, name=name: attempts.append(name) or []
    with app.test_request_context("/"):
        query = SearchQuery("synthetic query", [EngineRef(n, "general") for n in searx.search.PROCESSORS])
        search = searx.search.Search(query)
        result = search.search()
        rows = result.get_ordered_results()
        assert attempts[-2:] == ["bing2", "x402exa"], attempts
        assert len(attempts) == len(set(attempts)) == 6
        assert len(calls) == 2 and calls[0].content == calls[1].content
        assert len(rows) == 1 and rows[0].url == "https://example.org/item?id=1%2F2#x%2Fy"

    # Native pagination and concrete-language exclusions cannot pay.
    for page, lang in ((2, "all"), (1, "en")):
        with app.test_request_context("/"):
            query = SearchQuery("test", [EngineRef("x402exa", "general")], lang=lang, pageno=page)
            assert not searx.search.Search(query).search().main_results_map
    assert len(calls) == 2

    # A time-filtered query skips all current browser engines before admission.
    with _x402_admission._condition:
        _x402_admission._last_start = float("-inf")
    attempts.clear()
    with app.test_request_context("/"):
        query = SearchQuery("synthetic time", [EngineRef(n, "general") for n in searx.search.PROCESSORS], time_range="day")
        assert searx.search.Search(query).search().main_results_map
    assert attempts == ["x402exa"] and len(calls) == 3

    # Unexpected parser errors go through native suspension before release.
    processor = searx.search.PROCESSORS["x402exa"]
    with _x402_admission._condition:
        _x402_admission._last_start = float("-inf")
    with patch.object(processor.engine, "search", side_effect=ValueError("secret-canary")):
        with app.test_request_context("/"):
            query = SearchQuery("test", [EngineRef("x402exa", "general")])
            assert not searx.search.Search(query).search().main_results_map
    assert processor.suspended_status.is_suspended
    assert not _x402_admission._active and _x402_admission._token is None

print("PINNED_X402EXA_SCHEDULER_PAYMENT_CONTRACTS_OK")

# Force independent ordering calls to interleave inside the local sort key.
import searx.results
containers = [searx.results.ResultContainer(), searx.results.ResultContainer()]
for container in containers:
    container.extend("bing2", [{"url": "https://example.org/bing", "title": "Bing", "content": "b"}])
    container.extend("x402exa", [{"url": "https://example.org/exa", "title": "Exa", "content": "e"}])
    container.extend("bing2", [{"url": "https://example.org/exa", "title": "Exa", "content": "e"}])
function = searx.results.ResultContainer.get_ordered_results
original_key = function.__globals__["_wrapper_is_last_resort"]
original_global = searx.results.__dict__.get("sorted")
barrier = threading.Barrier(2)
local = threading.local()
def interleaved(name):
    if not getattr(local, "entered", False):
        local.entered = True
        barrier.wait(timeout=5)
    return original_key(name)
function.__globals__["_wrapper_is_last_resort"] = interleaved
ordered = []
threads = [threading.Thread(target=lambda c=c: ordered.append(c.get_ordered_results())) for c in containers]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join(timeout=10)
function.__globals__["_wrapper_is_last_resort"] = original_key
assert len(ordered) == 2 and all(rows[0].url.endswith("/exa") for rows in ordered)
assert searx.results.__dict__.get("sorted") is original_global
assert all(c.get_ordered_results() is c._main_results_sorted for c in containers)

# Real route ordering proves setup never executes an engine, even when enabled.
import searx.webapp as webapp
with patch.object(searx.search.Search, "search", side_effect=AssertionError("probe executed search")):
    with webapp.app.test_client() as client:
        response = client.post("/search", data={"format": "json", "q": ""})
        assert response.status_code == 400 and response.json == {"error": "No query"}
        with patch.dict(searx.settings["search"], {"formats": ["html"]}):
            assert client.post("/search", data={"format": "json", "q": ""}).status_code == 403

# Native selected_locale retains automatic-language provenance; ordinary
# query text 'test' is not a privileged setup sentinel.
searx.search.PROCESSORS["x402exa"].suspended_status.resume()
with _x402_admission._condition:
    _x402_admission._last_start = float("-inf")
with patch.object(_x402_payment, "exchange", fake_exchange), webapp.app.test_client() as client:
    before = len(calls)
    response = client.post("/search", data={"format": "json", "q": "test", "engines": "x402exa", "language": "auto"}, headers={"Accept-Language": "fr"})
    assert response.status_code == 200 and response.json["results"], response.status_code
    assert len(calls) == before + 1
    assert response.json["results"][0]["engines"] == ["x402exa"]
    before = len(calls)
    response = client.post("/search", data={"format": "json", "q": "test", "engines": "x402exa", "language": "fr"})
    assert response.status_code == 200 and not response.json["results"]
    assert len(calls) == before
print("PINNED_X402EXA_SCORING_AND_EMPTY_QUERY_OK")

# Native fan-out admission expiry is not a provider failure, including when
# capacity becomes available only after a waiter reacquires the condition.
processor = searx.search.PROCESSORS["x402exa"]
processor.suspended_status.resume()
with patch.object(processor.engine, "search", side_effect=AssertionError("expired admission dispatched")) as search:
    for delayed in (False, True):
        clock = [10.0]
        _x402_admission._last_start = float("-inf")
        held = _x402_admission.reserve(lambda: True) if delayed else None
        def expired_wakeup(_delay):
            clock[0] = 12.0
            _x402_admission.release(held)
        with patch.object(_x402_admission.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(_x402_admission._condition, "wait", side_effect=expired_wakeup):
            processor.search("fixture", {}, searx.results.ResultContainer(), 10.0 if delayed else 0.0, 2.0)
        assert not processor.suspended_status.is_suspended
        assert not _x402_admission._active and _x402_admission._token is None
    search.assert_not_called()

# Scheduler reservations bypass native wait(), so short budgets and delayed
# engine-thread dispatch must also expire without poisoning shared availability.
dispatch = processor.search
def delayed_dispatch(query, params, container, start_time, timeout_limit):
    # Simulate a delayed engine-thread entry while retaining the real caller's
    # join budget, so assertions cannot race the processor's cleanup.
    return dispatch(query, params, container, start_time - timeout_limit, timeout_limit)

for timeout, send in ((0.5, dispatch), (60, delayed_dispatch)):
    _x402_admission._last_start = float("-inf")
    with patch.object(processor.engine, "search", side_effect=AssertionError("expired reservation dispatched")) as search, \
            patch.object(processor, "search", send), app.test_request_context("/"):
        result = searx.search.Search(SearchQuery(
            "expired fixture", [EngineRef("x402exa", "general")], timeout_limit=timeout,
        )).search()
        search.assert_not_called()
        assert any(e.error_type == "api_admission_expired" for e in result.unresponsive_engines)
    assert not processor.suspended_status.is_suspended
    assert not _x402_admission._active and _x402_admission._token is None
    # A subsequent caller can immediately use the real engine and SDK path.
    before = len(calls)
    with patch.object(_x402_payment, "exchange", fake_exchange), app.test_request_context("/"):
        assert searx.search.Search(SearchQuery(
            "healthy fixture", [EngineRef("x402exa", "general")],
        )).search().main_results_map
    assert len(calls) == before + 1

# The native timeout marker is authoritative even before the numeric deadline.
_x402_admission._last_start = float("-inf")
token = _x402_admission.reserve(lambda: True)
assert token is not None
with patch.object(threading.current_thread(), "_timeout", True, create=True), \
        patch.object(processor.engine, "search", side_effect=AssertionError("timed-out owner dispatched")) as search:
    processor.search("fixture", {_x402_admission.RESERVATION_PARAM: token},
                     searx.results.ResultContainer(), time.monotonic(), 60)
    search.assert_not_called()
assert not processor.suspended_status.is_suspended
assert not _x402_admission._active and _x402_admission._token is None

# Competing real searches with duplicate paid-provider names must wait on API
# admission. The one worker-local cache needs only one discovery; every paid
# exchange still gets a fresh authorization. Keep the real three-second clock.
barrier = threading.Barrier(4)
starts = []
active = 0
peak = 0
calls.clear()
payment_client().requirements = None
_x402_admission._last_start = float("-inf")

async def concurrent_exchange(*args, **kwargs):
    global active, peak
    active += 1
    peak = max(peak, active)
    starts.append(time.monotonic())
    try:
        await asyncio.sleep(.05)
        return await exchange(*args, **kwargs, transport=httpx.MockTransport(respond))
    finally:
        active -= 1

def concurrent_search(index):
    barrier.wait(timeout=5)
    with app.test_request_context("/"):
        return bool(searx.search.Search(SearchQuery(
            "fixture " + str(index), [EngineRef("x402exa", "general")], lang="all",
        )).search().main_results_map)

with patch.dict(os.environ, {"SEARXNG_ROUND_ROBIN_PROVIDERS": " x402exa ,x402exa "}), \
        patch.object(_x402_payment, "exchange", concurrent_exchange), \
        concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    assert all(pool.map(concurrent_search, range(4)))
assert peak == 1 and len(calls) == 5
assert all(b - a >= 2.9 for a, b in zip(starts, starts[1:]))
from x402.http.utils import decode_payment_signature_header
nonces = [decode_payment_signature_header(r.headers["PAYMENT-SIGNATURE"]).payload["authorization"]["nonce"]
          for r in calls if "PAYMENT-SIGNATURE" in r.headers]
assert len(set(nonces)) == 4
assert not _x402_admission._active and _x402_admission._token is None

# Failure publication precedes release even with waiting real search calls.
starts.clear()
_x402_admission._last_start = float("-inf")
async def failed_exchange(*args, **kwargs):
    starts.append(time.monotonic())
    await asyncio.sleep(.05)
    raise ValueError("synthetic-failure")
with patch.object(_x402_payment, "exchange", failed_exchange), \
        concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    assert not any(pool.map(concurrent_search, range(4)))
assert len(starts) == 1 and processor.suspended_status.is_suspended
assert not _x402_admission._active and _x402_admission._token is None
print("PINNED_X402EXA_ADMISSION_CONCURRENCY_OK")
