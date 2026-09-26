"""Optional paid Exa API engine; offline means wrapper-owned HTTP transport."""

import asyncio
import time

engine_type = "offline"
categories = ["general"]
paging = False
time_range_support = True
language_support = False
safesearch = True
about = {
    "website": "https://exa.ai", "official_api_documentation": "https://exa.ai/docs/reference/search",
    "use_official_api": True, "require_api_key": False, "results": "JSON",
}


def search(query, params):
    from lxml.html import fromstring
    from private_onyx_obscura import normalize_public_url
    from searx.engines import _x402_payment as payment
    from x402_bootstrap import payment_client

    deadline = params["_wrapper_x402_deadline"]
    outcome = params["_wrapper_x402_outcome"]
    payment.check_deadline(deadline)
    body = payment.request_body(query, params)
    data = asyncio.run(payment.exchange(
        payment_client(), body, deadline, outcome, params["_wrapper_x402_start"],
    ))
    outcome.reason = "invalid_response"

    def plain_text(value):
        if not value.strip():
            return ""
        node = fromstring("<div>" + value + "</div>")
        for excluded in node.xpath(".//script|.//style|.//template|.//noscript"):
            excluded.drop_tree()
        return " ".join(node.text_content().split())

    def result_url(value, **kwargs):
        from private_onyx_obscura import ObscuraClientError
        try:
            return normalize_public_url(value, **kwargs)
        except ObscuraClientError:
            raise ValueError("invalid_result_url") from None

    payment.check_deadline(deadline)
    results = payment.parse_results(data, plain_text, result_url)
    payment.check_deadline(deadline)
    return results


def process(processor, query, params, container, start_time, timeout_limit):
    from searx.engines import _x402_admission as admission
    from searx.engines import _x402_payment as payment
    from searx.exceptions import SearxEngineAccessDeniedException

    token = params.get(admission.RESERVATION_PARAM)
    available = lambda: not processor.suspended_status.is_suspended
    try:
        if token is None:
            token = admission.wait(available, start_time + timeout_limit - 1)
        if token is None:
            if not processor.extend_container_if_suspended(container):
                processor.handle_exception(container, "api_admission_expired", suspend=False)
            return
        with admission.ownership(token) as record_start:
            if processor.extend_container_if_suspended(container):
                return
            deadline = min(time.monotonic() + 55, start_time + timeout_limit - 1)
            # Scheduler reservations bypass wait(); native waiters can also
            # expire between reservation and dispatch. Neither is a provider
            # failure. Check the native timeout marker at the same boundary.
            try:
                payment.check_deadline(deadline)
            except TimeoutError:
                processor.handle_exception(container, "api_admission_expired", suspend=False)
                return
            outcome = payment.Outcome()
            params["_wrapper_x402_outcome"] = outcome
            params["_wrapper_x402_deadline"] = deadline
            params["_wrapper_x402_start"] = record_start
            try:
                results = processor.engine.search(query, params)
                processor.extend_container(container, start_time, results)
            except (Exception, asyncio.CancelledError):
                # Never pass provider/SDK exceptions or their traceback to the
                # processor's metrics/logging: they may contain payment data.
                error = SearxEngineAccessDeniedException(
                    message=outcome.reason, suspended_time=outcome.suspension or (300 if outcome.submitted else 60),
                )
                # Native metrics use inspect.trace(), so establish a sanitized
                # traceback rather than exposing the currently handled SDK frame.
                try:
                    raise error from None
                except SearxEngineAccessDeniedException as safe_error:
                    safe_error.__context__ = None
                    processor.handle_exception(container, safe_error, suspend=True)
                processor.logger.warning("x402exa reason=%s payment=%s", outcome.reason, outcome.payment)
    finally:
        admission.release(token)
        for key in ("_wrapper_x402_outcome", "_wrapper_x402_deadline", "_wrapper_x402_start"):
            params.pop(key, None)
