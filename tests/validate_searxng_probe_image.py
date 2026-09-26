"""Pinned Onyx setup probe; all HTTP calls are in-process fixtures."""
from unittest.mock import patch
import requests
from onyx_wrapper_patches.api.searxng_retry import (
    apply_searxng_single_attempt_patch, apply_searxng_connection_probe_patch,
)
from onyx.tools.tool_implementations.web_search.clients import searxng_client as module

apply_searxng_single_attempt_patch()
apply_searxng_connection_probe_patch()
client = module.SearXNGClient("http://fixture.invalid")


def response(status, body, content_type="application/json"):
    from unittest.mock import Mock
    value = Mock(status_code=status, headers={"content-type": content_type})
    value.json.return_value = body
    return value


identity = response(200, {"brand": {"GIT_URL": "https://github.com/searxng/searxng"}})
for post in (
    response(400, {"error": "No query"}), response(200, {"error": "No query"}),
    response(400, {"error": "other"}), response(403, {}), response(302, {}),
    response(400, {"error": "No query"}, "text/html"),
):
    with patch.object(module.requests, "get", return_value=identity) as get, patch.object(module.requests, "post", return_value=post) as send:
        expected = post.status_code == 400 and post.json() == {"error": "No query"} and post.headers["content-type"] == "application/json"
        try:
            assert client.test_connection() == {"status": "ok"}
            assert expected
        except module.HTTPException:
            assert not expected
        assert get.call_count == send.call_count == 1
        for call in (get.call_args, send.call_args):
            assert call.kwargs["timeout"] == (5, 5) and call.kwargs["allow_redirects"] is False
        assert send.call_args.kwargs["data"] == {"q": "", "format": "json"}
for side_effect in (requests.Timeout(), ValueError("malformed JSON")):
    with patch.object(module.requests, "get", side_effect=side_effect) as get, patch.object(module.requests, "post") as send:
        try:
            client.test_connection()
            raise AssertionError("failure accepted")
        except module.HTTPException:
            pass
        assert get.call_count == 1 and send.call_count == 0
assert not hasattr(module.SearXNGClient.search, "__wrapped__")
print("PINNED_SEARXNG_NON_SEARCH_PROBE_OK")
