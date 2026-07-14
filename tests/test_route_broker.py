from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, patch


CRW_DIR = Path(__file__).resolve().parents[1] / "crw"
MODULE_PATH = CRW_DIR / "route_broker.py"
CREDENTIAL = "a" * 64


def _load_module(env_overrides: dict[str, str] | None = None) -> ModuleType:
    spec = importlib.util.spec_from_file_location("route_broker_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    env = {
        "EGRESS_PROXY_POLICY": "onyx-helper",
        "EGRESS_ROUTE_CLASS": "public",
        "EGRESS_PROXY_ALLOWED_CLIENT_HOSTS": "test-policy",
        "EGRESS_BROKER_ALLOWED_CLIENT_HOST": "test-policy",
        "EGRESS_ROUTE_BROKER_CREDENTIAL": CREDENTIAL,
        "MYST_VPN_ENABLED": "false",
        "EGRESS_UPSTREAM_PROXY_URL": "",
    }
    env.update(env_overrides or {})
    with patch.dict(os.environ, env, clear=True), patch.object(
        sys, "path", [str(CRW_DIR), *sys.path]
    ):
        sys.modules.pop("prefetch_blocking_proxy", None)
        spec.loader.exec_module(module)
    return module


class _Writer:
    def __init__(self) -> None:
        self.data = bytearray()
        self.closed = False

    def get_extra_info(self, name: str):  # noqa: ANN201
        return ("172.20.0.2", 12345) if name == "peername" else None

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        return None

    def write_eof(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


def _reader(payload: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(payload)
    reader.feed_eof()
    return reader


class RouteBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_credential_is_denied_before_destination_validation(self) -> None:
        module = _load_module()
        request = {
            "version": module.MAGIC,
            "credential": "b" * 64,
            "route_class": "public",
            "transport": "opaque",
            "host": "example.com",
            "port": 443,
        }
        writer = _Writer()
        validate = AsyncMock()
        with patch.object(module, "_allowed_peer", AsyncMock(return_value=True)), patch.object(
            module.policy, "_validate_destination", validate
        ):
            await module.handle_client(
                _reader((json.dumps(request) + "\n").encode()), writer
            )

        response = json.loads(writer.data)
        self.assertEqual(response["status"], "denied")
        self.assertIn("authentication", response["reason"])
        validate.assert_not_awaited()
        self.assertTrue(writer.closed)

    async def test_wrong_route_class_is_denied(self) -> None:
        module = _load_module()
        request = {
            "version": module.MAGIC,
            "credential": CREDENTIAL,
            "route_class": "host",
            "transport": "opaque",
            "host": "example.com",
            "port": 443,
        }
        writer = _Writer()
        with patch.object(module, "_allowed_peer", AsyncMock(return_value=True)):
            await module.handle_client(
                _reader((json.dumps(request) + "\n").encode()), writer
            )
        self.assertIn("wrong route class", json.loads(writer.data)["reason"])

    async def test_oversized_frame_is_denied(self) -> None:
        module = _load_module()
        writer = _Writer()
        with patch.object(module, "_allowed_peer", AsyncMock(return_value=True)):
            await module.handle_client(
                _reader(b"x" * (module.MAX_REQUEST_BYTES + 1) + b"\n"), writer
            )
        self.assertIn("framing", json.loads(writer.data)["reason"])

    async def test_valid_request_revalidates_and_opens_only_pinned_route(self) -> None:
        module = _load_module()
        request = {
            "version": module.MAGIC,
            "credential": CREDENTIAL,
            "route_class": "public",
            "transport": "opaque",
            "host": "example.com",
            "port": 443,
        }
        writer = _Writer()
        upstream_reader = _reader(b"")
        upstream_writer = _Writer()
        validate = AsyncMock(return_value=(None, ("93.184.216.34",)))
        connect = AsyncMock(return_value=(upstream_reader, upstream_writer))
        with patch.object(module, "_allowed_peer", AsyncMock(return_value=True)), patch.object(
            module.policy, "_validate_destination", validate
        ), patch.object(module.policy, "_connect_via_upstream", connect):
            await module.handle_client(
                _reader((json.dumps(request) + "\n").encode()), writer
            )

        self.assertTrue(writer.data.startswith(b'{"status":"ok"}\n'))
        validate.assert_awaited_once_with("example.com", 443)
        connect.assert_awaited_once_with(
            "example.com", 443, ("93.184.216.34",)
        )

    async def test_host_route_allows_cleartext_to_validated_rfc1918(self) -> None:
        module = _load_module(
            {
                "EGRESS_ROUTE_CLASS": "host",
                "EGRESS_ALLOW_RFC1918": "true",
            }
        )
        request = {
            "version": module.MAGIC,
            "credential": CREDENTIAL,
            "route_class": "host",
            "transport": "cleartext",
            "host": "inference.internal",
            "port": 8080,
        }
        writer = _Writer()
        validate = AsyncMock(return_value=(None, ("192.168.1.20",)))
        connect = AsyncMock(return_value=(_reader(b""), _Writer()))
        with patch.object(module, "_allowed_peer", AsyncMock(return_value=True)), patch.object(
            module.policy, "_validate_destination", validate
        ), patch.object(module.policy, "_connect_via_upstream", connect):
            await module.handle_client(
                _reader((json.dumps(request) + "\n").encode()), writer
            )

        self.assertTrue(writer.data.startswith(b'{"status":"ok"}\n'))
        connect.assert_awaited_once_with(
            "inference.internal", 8080, ("192.168.1.20",)
        )

    async def test_host_route_denies_cleartext_to_global_answer(self) -> None:
        module = _load_module(
            {
                "EGRESS_ROUTE_CLASS": "host",
                "EGRESS_ALLOW_RFC1918": "true",
            }
        )
        request = {
            "version": module.MAGIC,
            "credential": CREDENTIAL,
            "route_class": "host",
            "transport": "cleartext",
            "host": "public.example",
            "port": 80,
        }
        writer = _Writer()
        validate = AsyncMock(return_value=(None, ("93.184.216.34",)))
        connect = AsyncMock()
        with patch.object(module, "_allowed_peer", AsyncMock(return_value=True)), patch.object(
            module.policy, "_validate_destination", validate
        ), patch.object(module.policy, "_connect_via_upstream", connect):
            await module.handle_client(
                _reader((json.dumps(request) + "\n").encode()), writer
            )

        response = json.loads(writer.data)
        self.assertEqual(response["status"], "denied")
        self.assertIn("HTTP URLs are disabled", response["reason"])
        connect.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
