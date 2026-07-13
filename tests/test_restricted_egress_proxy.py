from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "crw" / "prefetch_blocking_proxy.py"


def _load_module(
    policy: str = "prefetch", env_overrides: dict[str, str] | None = None
) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"restricted_egress_proxy_{policy}", MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    env = {
        "EGRESS_PROXY_POLICY": policy,
        "EGRESS_PROXY_ALLOWED_CLIENT_HOSTS": "test-bridge",
        "ONYX_AGENT_OUTBOUND_PROXY_URL": "",
        "ONYX_AGENT_ALLOW_HTTP_URLS": "false",
    }
    env.update(env_overrides or {})
    with patch.dict(os.environ, env, clear=True):
        spec.loader.exec_module(module)
    return module


class RestrictedEgressProxyTests(unittest.IsolatedAsyncioTestCase):
    class _Writer:
        def __init__(self) -> None:
            self.data = bytearray()

        def write(self, data: bytes) -> None:
            self.data.extend(data)

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            return None

    async def test_direct_connection_uses_only_validated_address(self) -> None:
        module = _load_module()
        fake_reader = object()
        fake_writer = object()
        open_connection = AsyncMock(return_value=(fake_reader, fake_writer))
        resolve = AsyncMock(return_value={"93.184.216.34"})

        with patch.object(module, "_resolve_host", resolve), patch.object(
            module.asyncio, "open_connection", open_connection
        ):
            reason, validated_ips = await module._validate_destination(
                "public.example", 443
            )
            self.assertIsNone(reason)
            result = await module._connect_via_upstream(
                "public.example", 443, validated_ips
            )

        self.assertEqual(result, (fake_reader, fake_writer))
        resolve.assert_awaited_once_with("public.example", 443)
        open_connection.assert_awaited_once_with("93.184.216.34", 443)

    async def test_validation_rejects_private_dns_answer(self) -> None:
        module = _load_module()
        with patch.object(
            module, "_resolve_host", AsyncMock(return_value={"192.168.1.20"})
        ):
            reason, addresses = await module._validate_destination(
                "public.example", 443
            )
        self.assertIn("blocked", reason or "")
        self.assertEqual(addresses, ())

    def test_browser_policy_allows_search_hosts(self) -> None:
        module = _load_module("browser")
        self.assertFalse(module.BLOCK_SEARCH_ENGINES)
        self.assertTrue(module._is_search_engine("www.google.com"))

    def test_search_policy_normalizes_fully_qualified_hostnames(self) -> None:
        module = _load_module("executor")
        self.assertTrue(module._is_search_engine("google.com."))
        self.assertTrue(module._is_search_engine("WWW.GOOGLE.COM."))

    def test_upstream_proxy_logging_redacts_credentials(self) -> None:
        module = _load_module(
            env_overrides={
                "ONYX_AGENT_OUTBOUND_PROXY_URL": "https://user:secret@proxy.example:8443"
            }
        )
        module._validate_upstream_proxy_config()
        self.assertEqual(
            module._sanitized_upstream_proxy(), "https://proxy.example:8443"
        )
        self.assertNotIn("secret", module._sanitized_upstream_proxy())

    def test_invalid_upstream_proxy_fails_validation(self) -> None:
        module = _load_module(
            env_overrides={"ONYX_AGENT_OUTBOUND_PROXY_URL": "ftp://proxy.example"}
        )
        with self.assertRaisesRegex(RuntimeError, "must use http"):
            module._validate_upstream_proxy_config()

    def test_request_framing_accepts_identical_content_lengths(self) -> None:
        module = _load_module()
        self.assertEqual(
            module._parse_request_framing(
                [("content-length", "5"), ("content-length", "5, 5")]
            ),
            (5, ()),
        )

    def test_request_framing_rejects_ambiguous_lengths(self) -> None:
        module = _load_module()
        with self.assertRaisesRegex(ValueError, "conflicting Content-Length"):
            module._parse_request_framing(
                [("content-length", "5"), ("content-length", "6")]
            )
        with self.assertRaisesRegex(ValueError, "must not be combined"):
            module._parse_request_framing(
                [("content-length", "5"), ("transfer-encoding", "chunked")]
            )

    def test_request_framing_requires_final_single_chunked_coding(self) -> None:
        module = _load_module()
        self.assertEqual(
            module._parse_request_framing(
                [("transfer-encoding", "gzip, chunked")]
            ),
            (None, ("gzip", "chunked")),
        )
        with self.assertRaisesRegex(ValueError, "exactly one chunked"):
            module._parse_request_framing(
                [("transfer-encoding", "chunked, gzip")]
            )

    async def test_chunked_request_body_is_forwarded_with_trailers(self) -> None:
        module = _load_module()
        encoded = (
            b"4\r\nWiki\r\n"
            b"5;source=test\r\npedia\r\n"
            b"0\r\nX-Checksum: yes\r\n\r\n"
        )
        reader = module.asyncio.StreamReader()
        reader.feed_data(encoded)
        reader.feed_eof()
        writer = self._Writer()

        await module._forward_request_body(reader, writer, None, ("chunked",))

        self.assertEqual(bytes(writer.data), encoded)

    async def test_chunked_request_rejects_framing_trailer(self) -> None:
        module = _load_module()
        reader = module.asyncio.StreamReader()
        reader.feed_data(b"0\r\nContent-Length: 5\r\n\r\n")
        reader.feed_eof()
        writer = self._Writer()

        with self.assertRaisesRegex(ValueError, "forbidden field"):
            await module._forward_request_body(
                reader, writer, None, ("chunked",)
            )

    async def test_forward_http_normalizes_and_streams_chunked_request(self) -> None:
        module = _load_module(
            env_overrides={"ONYX_AGENT_ALLOW_HTTP_URLS": "true"}
        )
        encoded_body = b"4\r\ntest\r\n0\r\n\r\n"
        client_reader = module.asyncio.StreamReader()
        client_reader.feed_data(encoded_body)
        client_reader.feed_eof()
        client_writer = self._Writer()
        upstream_reader = module.asyncio.StreamReader()
        upstream_reader.feed_eof()
        upstream_writer = self._Writer()

        with patch.object(
            module,
            "_reject_blocked_destination",
            AsyncMock(return_value=(False, ("93.184.216.34",))),
        ), patch.object(
            module,
            "_open_plain_http_forward_connection",
            AsyncMock(return_value=(upstream_reader, upstream_writer, False)),
        ):
            await module._handle_forward_http(
                "POST",
                "http://example.com/upload",
                [
                    ("host", "attacker.invalid"),
                    ("transfer-encoding", "chunked"),
                    ("content-type", "text/plain"),
                ],
                client_reader,
                client_writer,
                ("test", 1),
            )

        forwarded = bytes(upstream_writer.data)
        self.assertIn(b"host: example.com\r\n", forwarded)
        self.assertNotIn(b"attacker.invalid", forwarded)
        self.assertIn(b"transfer-encoding: chunked\r\n", forwarded)
        self.assertTrue(forwarded.endswith(encoded_body))

    async def test_policy_listener_accepts_only_resolved_bridge_ip(self) -> None:
        module = _load_module()
        with patch.object(
            module, "_resolve_host", AsyncMock(return_value={"172.30.0.5"})
        ):
            self.assertIsNone(
                await module._allowed_client_reason(("172.30.0.5", 12345))
            )
            self.assertIn(
                "not an allowed bridge",
                await module._allowed_client_reason(("172.30.0.6", 12345)) or "",
            )

    async def test_connect_port_80_is_rejected_when_http_is_disabled(self) -> None:
        module = _load_module("browser")
        writer = self._Writer()
        with patch.object(module, "_connect_via_upstream", AsyncMock()) as connect:
            await module._handle_connect(
                "example.com:80", AsyncMock(), writer, ("test", 1)
            )
        self.assertIn(b"403 Forbidden", writer.data)
        connect.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
