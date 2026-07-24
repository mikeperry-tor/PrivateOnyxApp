"""Strict stock-Onyx integration for the wrapper's direct Obscura client."""

from __future__ import annotations

import contextvars
import inspect
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from private_onyx_obscura import BodyClassification
from private_onyx_obscura import FetchFailure
from private_onyx_obscura import ObscuraClientError
from private_onyx_obscura import fetch_sync
from private_onyx_obscura import validate_wait_until

OPEN_URL_TIMEOUT_SECONDS = 120
BROWSER_ATTEMPT_TIMEOUT_SECONDS = 105.0
RESULT_COLLECTION_HEADROOM_SECONDS = 5.0
ACTIVE_FETCHES = threading.BoundedSemaphore(5)
_STATE: contextvars.ContextVar["InvocationState | None"] = contextvars.ContextVar(
    "wrapper_open_url_invocation_state", default=None
)

HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
RAW_TEXT_TYPES = frozenset(
    {
        "text/plain", "text/markdown", "text/csv", "text/tab-separated-values",
        "application/json", "application/ld+json", "application/xml", "text/xml",
        "application/yaml", "application/x-yaml", "text/yaml", "text/x-yaml",
        "application/toml", "text/css", "application/javascript", "text/javascript",
    }
)
SOURCE_SUFFIXES = frozenset(
    {
        ".txt", ".md", ".rst", ".csv", ".tsv", ".json", ".jsonl", ".xml",
        ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".log", ".py",
        ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".java", ".c", ".h",
        ".cc", ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".sh",
        ".bash", ".zsh", ".fish", ".sql", ".css", ".scss", ".less",
    }
)
SEMANTIC_CHARSETS = frozenset({None, "utf-8", "utf8", "us-ascii", "ascii"})
EXACT_CHARSETS = frozenset(
    {None, "utf-8", "utf8", "us-ascii", "ascii", "iso-8859-1", "latin-1", "windows-1252"}
)


@dataclass
class InvocationState:
    deadline: float
    finalized: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def finish(self) -> None:
        with self.lock:
            self.finalized = True

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def permits_navigation(self) -> bool:
        with self.lock:
            return not self.finalized and self.deadline > time.monotonic()

@dataclass(frozen=True)
class CrawledSection:
    requested_url: str
    terminal_url: str
    section: object


def _parse_document_limit() -> int:
    raw = os.environ.get("ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB", "20")
    if not raw or not raw.isdecimal():
        raise RuntimeError("ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB must be a positive base-10 integer")
    mib = int(raw)
    if mib <= 0 or mib > ((1 << 63) - 1) // (1024 * 1024):
        raise RuntimeError("ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB is outside the supported range")
    return mib * 1024 * 1024


DOCUMENT_LIMIT_BYTES = _parse_document_limit()
WAIT_UNTIL = validate_wait_until(
    os.environ.get("OBSCURA_BROWSER_WAIT_UNTIL_WEB", "domcontentloaded")
)
CDP_URL = os.environ.get(
    "ONYX_OBSCURA_CDP_URL", "ws://obscura-cdp-gateway:9222/devtools/browser"
)
ALLOW_HTTP = os.environ.get("EGRESS_ALLOW_HTTP_URLS", "false").lower() in {
    "1", "true", "yes", "on"
}


def _parse_allow_http_onion() -> bool:
    raw = os.environ.get("EGRESS_ALLOW_HTTP_ONION_URLS", "false").strip().lower()
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise RuntimeError("EGRESS_ALLOW_HTTP_ONION_URLS must be exactly true or false")


ALLOW_HTTP_ONION = _parse_allow_http_onion()


def _failure(url: str, reason: str):
    from onyx.tools.tool_implementations.open_url.models import WebContent

    return WebContent(
        title="", link=url, full_content="", published_date=None,
        scrape_successful=False, failure_reason=reason,
    )


def _reason(exc: ObscuraClientError) -> str:
    return {
        FetchFailure.INVALID_URL: "URL is invalid or forbidden by public-only browser policy",
        FetchFailure.POLICY_DENIED: "destination was denied by the final-hop policy",
        FetchFailure.PRE_NAVIGATION_TIMEOUT: "browser setup timed out before navigation",
        FetchFailure.POST_NAVIGATION_TIMEOUT: "browser response processing timed out",
        FetchFailure.TRANSPORT: "browser proxy could not resolve or connect to destination",
        FetchFailure.NAVIGATION_TIMEOUT: "browser navigation timed out",
        FetchFailure.OVERSIZE: "response exceeded the configured maximum size",
        FetchFailure.BODY_UNAVAILABLE: "same-navigation response body was unavailable",
        FetchFailure.BYTE_IDENTITY_UNAVAILABLE: "original document bytes were unavailable",
        FetchFailure.UNSUPPORTED_CHARSET: "response charset is unsupported",
        FetchFailure.EMPTY_CONTENT: "browser response was empty",
        FetchFailure.PROTOCOL: f"browser protocol failed during {exc.stage}",
        FetchFailure.FINALIZED: "open_url invocation timed out before navigation",
    }.get(exc.category, "browser request failed")


def _collect_url_results(
    urls,
    state: InvocationState,
    fetch_one,
    failure_factory,
    *,
    max_workers: int = 5,
    headroom_seconds: float = RESULT_COLLECTION_HEADROOM_SECONDS,
):
    if not urls:
        return []
    executor = ThreadPoolExecutor(max_workers=min(max_workers, len(urls)))
    future_to_index = {
        executor.submit(fetch_one, target): index
        for index, target in enumerate(urls)
    }
    results = [None] * len(urls)
    try:
        collection_timeout = max(0.0, state.remaining() - headroom_seconds)
        done, pending = wait(future_to_index, timeout=collection_timeout)
        for future in done:
            index = future_to_index[future]
            try:
                results[index] = future.result()
            except Exception:
                results[index] = failure_factory(
                    urls[index], "browser worker failed before producing a result"
                )
        if pending:
            state.finish()
            for future in pending:
                index = future_to_index[future]
                future.cancel()
                results[index] = failure_factory(
                    urls[index], "open_url result collection deadline expired"
                )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return results


def _decode_raw(result) -> str:
    if result.body is None:
        raise ObscuraClientError(FetchFailure.BODY_UNAVAILABLE, "dispatch", "body missing")
    if result.body_classification is BodyClassification.TEXT:
        if result.charset not in SEMANTIC_CHARSETS:
            raise ObscuraClientError(
                FetchFailure.UNSUPPORTED_CHARSET, "dispatch", "text charset is unsupported"
            )
        return result.body.decode("utf-8", "strict")
    if result.charset not in EXACT_CHARSETS:
        raise ObscuraClientError(
            FetchFailure.UNSUPPORTED_CHARSET, "dispatch", "binary charset is unsupported"
        )
    return result.body.decode(result.charset or "utf-8", "strict")


def _direct_fetch(url: str, state: InvocationState):
    from onyx.file_processing.html_utils import web_html_cleanup
    from onyx.tools.tool_implementations.open_url.models import WebContent
    from onyx.utils.web_content import extract_pdf_text
    from onyx.utils.web_content import title_from_pdf_metadata
    from onyx.utils.web_content import title_from_url

    acquired = ACTIVE_FETCHES.acquire(timeout=state.remaining())
    if not acquired:
        return _failure(url, "open_url invocation timed out before browser capacity was available")
    try:
        if not state.permits_navigation():
            return _failure(url, "open_url invocation timed out before navigation")
        attempt_timeout = min(
            BROWSER_ATTEMPT_TIMEOUT_SECONDS,
            state.remaining() - RESULT_COLLECTION_HEADROOM_SECONDS,
        )
        if attempt_timeout <= 0:
            return _failure(url, "open_url invocation timed out before navigation")
        result = fetch_sync(
            url,
            cdp_url=CDP_URL,
            wait_until=WAIT_UNTIL,
            allow_http=ALLOW_HTTP,
            allow_http_onion=ALLOW_HTTP_ONION,
            body_limit=DOCUMENT_LIMIT_BYTES,
            dom_limit=DOCUMENT_LIMIT_BYTES,
            want="both",
            request_timeout_seconds=attempt_timeout,
            pre_navigation_guard=state.permits_navigation,
        )
        if result.challenge is not None:
            return _failure(url, f"browser request was blocked ({result.challenge.value})")
        if result.status >= 400:
            return _failure(url, f"upstream returned HTTP {result.status}")
        if not state.permits_navigation():
            return _failure(url, "open_url invocation finalized during browser navigation")

        path_suffix = PurePosixPath(urlsplit(result.final_url).path).suffix.lower()
        pdf_signal = (
            result.content_type == "application/pdf"
            or path_suffix == ".pdf"
            or (result.body or b"")[:5] == b"%PDF-"
        )
        if pdf_signal:
            if result.body_classification is not BodyClassification.BINARY or result.body is None:
                return _failure(url, "PDF original-byte identity was unavailable")
            text, metadata = extract_pdf_text(result.body)
            if not text.strip():
                return _failure(url, "PDF could not be parsed into readable text")
            content = WebContent(
                title=title_from_pdf_metadata(metadata) or title_from_url(result.final_url),
                link=result.final_url, full_content=text, published_date=None,
                scrape_successful=True,
            )
            return content if state.permits_navigation() else _failure(
                url, "open_url invocation finalized during document parsing"
            )

        if result.content_type in HTML_TYPES:
            if not result.rendered_html:
                return _failure(url, "rendered page was empty")
            parsed = web_html_cleanup(result.rendered_html)
            text = parsed.cleaned_text or ""
            if not text.strip():
                return _failure(url, "rendered page could not be parsed into readable text")
            content = WebContent(
                title=parsed.title or "", link=result.final_url,
                full_content=text, published_date=None, scrape_successful=True,
            )
            return content if state.permits_navigation() else _failure(
                url, "open_url invocation finalized during HTML processing"
            )

        source_path = path_suffix in SOURCE_SUFFIXES and result.content_type in {
            None, "text/plain", "application/octet-stream"
        }
        if result.content_type in RAW_TEXT_TYPES or source_path:
            text = _decode_raw(result)
            if not text.strip():
                return _failure(url, "response was empty")
            content = WebContent(
                title=title_from_url(result.final_url), link=result.final_url,
                full_content=text, published_date=None, scrape_successful=True,
            )
            return content if state.permits_navigation() else _failure(
                url, "open_url invocation finalized during text processing"
            )
        return _failure(url, "response content type is unsupported")
    except ObscuraClientError as exc:
        return _failure(url, _reason(exc))
    except Exception:
        return _failure(url, "browser content processing failed")
    finally:
        ACTIVE_FETCHES.release()


def install() -> None:
    from onyx.tools.tool_implementations.open_url import onyx_web_crawler as crawler
    from onyx.tools.tool_implementations.open_url import open_url_tool
    from onyx.tools.tool_implementations.open_url.models import FailedFetch
    from onyx.tools.tool_implementations.open_url.utils import (
        filter_web_contents_with_no_title_or_content,
    )
    from onyx.tools.tool_implementations.web_search.utils import (
        inference_section_from_internet_page_scrape,
    )
    from onyx.utils.url import normalize_url as normalize_web_content_url

    required = ("ssrf_safe_get(", "fetch_rendered_html(", "ThreadPoolExecutor")
    source = inspect.getsource(crawler.OnyxWebCrawler)
    missing = [fragment for fragment in required if fragment not in source]
    if missing or crawler.DEFAULT_MAX_WORKERS != 5 or open_url_tool.OPEN_URL_TIMEOUT_SECONDS != 120:
        raise RuntimeError(f"Onyx direct-Obscura patch source drift: {missing!r}")

    original_run = open_url_tool.OpenURLTool.run
    original_fetch_web_content = open_url_tool.OpenURLTool._fetch_web_content
    original_merge = open_url_tool.OpenURLTool._merge_indexed_and_crawled_results

    def _run(self, *args, **kwargs):
        state = InvocationState(time.monotonic() + OPEN_URL_TIMEOUT_SECONDS)
        token = _STATE.set(state)
        timer = threading.Timer(OPEN_URL_TIMEOUT_SECONDS, state.finish)
        timer.daemon = True
        timer.start()
        try:
            return original_run(self, *args, **kwargs)
        finally:
            state.finish()
            timer.cancel()
            _STATE.reset(token)

    def _contents(self, urls):
        if not urls:
            return []
        state = _STATE.get() or InvocationState(
            time.monotonic() + OPEN_URL_TIMEOUT_SECONDS
        )
        return _collect_url_results(
            urls,
            state,
            lambda target: _direct_fetch(target, state),
            _failure,
        )

    def _fetch_web_content(self, urls, url_snippet_map):
        if not isinstance(self._provider, crawler.OnyxWebCrawler):
            return original_fetch_web_content(self, urls, url_snippet_map)
        if not urls:
            return [], []
        raw_web_contents = self._provider.contents(urls)
        if len(raw_web_contents) != len(urls):
            raise RuntimeError(
                "built-in crawler returned a result count different from its input count"
            )

        failed_by_url = {}

        def _mark_failed(requested_url, reason):
            existing = failed_by_url.get(requested_url)
            if existing is None or (reason and not existing.failure_reason):
                failed_by_url[requested_url] = FailedFetch(
                    url=requested_url, failure_reason=reason
                )

        records = []
        for requested_url, content in zip(urls, raw_web_contents, strict=True):
            if not content.title.strip() and not content.full_content.strip():
                _mark_failed(requested_url, content.failure_reason)
                continue
            if not filter_web_contents_with_no_title_or_content([content]):
                _mark_failed(requested_url, content.failure_reason)
                continue
            text = content.full_content.strip()
            insufficient = not text or text.lower() == "loading..." or len(text) < 50
            if content.scrape_successful and content.full_content and not insufficient:
                section = inference_section_from_internet_page_scrape(
                    content, url_snippet_map.get(requested_url, "")
                )
                records.append(
                    CrawledSection(requested_url, content.link, section)
                )
            else:
                _mark_failed(requested_url, content.failure_reason)
        return records, list(failed_by_url.values())

    def _merge_results(
        self,
        indexed_sections,
        crawled_sections,
        url_to_doc_id,
        all_urls,
        failed_web_fetches,
    ):
        if not crawled_sections or not all(
            isinstance(item, CrawledSection) for item in crawled_sections
        ):
            return original_merge(
                self, indexed_sections, crawled_sections, url_to_doc_id,
                all_urls, failed_web_fetches,
            )
        from open_url_failure_reporting_patch import record_failures

        record_failures(failed_web_fetches)
        indexed_by_doc_id = {
            section.center_chunk.document_id: section for section in indexed_sections
        }
        crawled_by_requested = {
            normalize_web_content_url(item.requested_url): item.section
            for item in crawled_sections
        }
        merged = []
        used_doc_ids = set()
        for requested_url in all_urls:
            doc_id = url_to_doc_id.get(requested_url)
            indexed = indexed_by_doc_id.get(doc_id) if doc_id else None
            crawled = crawled_by_requested.get(
                normalize_web_content_url(requested_url)
            )
            if indexed is not None and indexed.combined_content:
                merged.append(indexed)
                if doc_id:
                    used_doc_ids.add(doc_id)
            elif crawled is not None and crawled.combined_content:
                merged.append(crawled)
        merged.extend(
            section
            for doc_id, section in indexed_by_doc_id.items()
            if doc_id not in used_doc_ids
        )
        return merged

    open_url_tool.OpenURLTool.run = _run
    open_url_tool.OpenURLTool._fetch_web_content = _fetch_web_content
    open_url_tool.OpenURLTool._merge_indexed_and_crawled_results = _merge_results
    crawler.OnyxWebCrawler.contents = _contents
    crawler.OnyxWebCrawler._fetch_url = lambda self, url: _direct_fetch(
        url, _STATE.get() or InvocationState(time.monotonic() + OPEN_URL_TIMEOUT_SECONDS)
    )
    print(
        "sitecustomize_api_server: installed strict direct Obscura crawler "
        f"(document_limit_bytes={DOCUMENT_LIMIT_BYTES})",
        flush=True,
    )
