"""One explicitly requested search through an already-running SearXNG service."""

import json
import sys
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def run():
    # Executed inside the existing SearXNG container through Make/Compose.
    # Key handling and validation remain the normal service startup boundary.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open("http://127.0.0.1:8888/config", timeout=5) as response:
        config = json.load(response)
    if not any(engine.get("name") == "x402exa" for engine in config.get("engines", [])):
        raise ValueError("running stack has no x402exa engine")
    body = urlencode({"q": "Python programming language official documentation", "format": "json",
                      "engines": "x402exa", "language": "all"}).encode()
    # Exactly one search submission. A timeout never triggers replay.
    with opener.open(Request("http://127.0.0.1:8888/search", data=body), timeout=180) as response:
        data = json.load(response)
    results = data.get("results")
    if not results or not all(
        "x402exa" in row.get("engines", []) and row.get("content", "").strip()
        and urlsplit(row.get("url", "")).scheme in ("http", "https") for row in results
    ):
        raise ValueError("search did not qualify the result path")
    print("x402exa live result path qualified; relies on Exa settlement-before-results, not independent proof of a debit. An unsolicited unpaid success is indistinguishable here.")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        print("x402exa live qualification failed or is ambiguous; no retry was sent.", file=sys.stderr)
        raise SystemExit(1) from None
