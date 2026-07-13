from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "crw" / "prefetch_blocking_proxy.py"


def _load_module(policy: str = "prefetch") -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"restricted_egress_proxy_{policy}", MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        os.environ,
        {
            "EGRESS_PROXY_POLICY": policy,
            "ONYX_AGENT_OUTBOUND_PROXY_URL": "",
            "ONYX_AGENT_ALLOW_HTTP_URLS": "false",
        },
        clear=False,
    ):
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
