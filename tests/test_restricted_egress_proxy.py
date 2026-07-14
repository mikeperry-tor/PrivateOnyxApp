from __future__ import annotations

import importlib.util
import ipaddress
import os
import struct
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, Mock, patch


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
        "MYST_VPN_ENABLED": "true",
        "EGRESS_UPSTREAM_PROXY_URL": "",
        "EGRESS_ALLOW_HTTP_URLS": "false",
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

        async def wait_closed(self) -> None:
            return None

    async def test_direct_connection_uses_only_validated_address(self) -> None:
        module = _load_module()
        fake_reader = object()
        fake_writer = object()
        open_connection = AsyncMock(return_value=(fake_reader, fake_writer))
        resolve = AsyncMock(return_value={"93.184.216.34"})

        with patch.object(module, "_resolve_target_host", resolve), patch.object(
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
            module,
            "_resolve_target_host",
            AsyncMock(return_value={"192.168.1.20"}),
        ):
            reason, addresses = await module._validate_destination(
                "public.example", 443
            )
        self.assertIn("blocked", reason or "")
        self.assertEqual(addresses, ())

    async def test_public_route_rejects_exact_host_exception(self) -> None:
        module = _load_module(
            "onyx-helper",
            {"EGRESS_ROUTE_CLASS": "public"},
        )
        reason, addresses = await module._validate_destination(
            "host.docker.internal", 9150
        )
        self.assertIsNotNone(reason)
        self.assertEqual(addresses, ())

    async def test_host_proxy_resolves_exact_host_itself(self) -> None:
        module = _load_module(
            "onyx-helper",
            {"EGRESS_ROUTE_CLASS": "host"},
        )
        with patch.object(
            module,
            "_resolve_system_host",
            AsyncMock(return_value={"192.168.65.2"}),
        ) as resolve:
            reason, addresses = await module._validate_destination(
                "host.docker.internal", 9150
            )
        self.assertIsNone(reason)
        self.assertEqual(addresses, ("192.168.65.2",))
        resolve.assert_awaited_once_with("host.docker.internal", 9150)

    async def test_exact_host_exception_rejects_non_unicast_answers(self) -> None:
        module = _load_module(
            "onyx-helper",
            {
                "EGRESS_ROUTE_CLASS": "host",
            },
        )
        forbidden_addresses = (
            "0.0.0.0",
            "::",
            "240.0.0.1",
            "127.0.0.1",
            "169.254.1.1",
        )
        for address in forbidden_addresses:
            with self.subTest(address=address), patch.object(
                module,
                "_resolve_system_host",
                AsyncMock(return_value={address}),
            ):
                reason, addresses = await module._validate_destination(
                    "host.docker.internal", 9150
                )
            self.assertEqual(
                reason, "host exception resolved to a forbidden address"
            )
            self.assertEqual(addresses, ())

    async def test_exact_host_exception_accepts_private_unicast_answer(self) -> None:
        module = _load_module(
            "onyx-helper",
            {
                "EGRESS_ROUTE_CLASS": "host",
            },
        )
        with patch.object(
            module,
            "_resolve_system_host",
            AsyncMock(return_value={"192.168.65.2"}),
        ):
            reason, addresses = await module._validate_destination(
                "host.docker.internal", 9150
            )
        self.assertIsNone(reason)
        self.assertEqual(addresses, ("192.168.65.2",))

    def test_plain_http_private_exceptions_are_host_route_only(self) -> None:
        host_module = _load_module(
            "onyx-helper",
            {
                "EGRESS_ROUTE_CLASS": "host",
                "EGRESS_ALLOW_RFC1918": "true",
            },
        )
        self.assertTrue(
            host_module._plain_http_allowed("host.docker.internal", 3210)
        )
        self.assertTrue(
            host_module._plain_http_allowed(
                "192.168.1.20", 3210, ("192.168.1.20",)
            )
        )
        self.assertTrue(
            host_module._plain_http_allowed(
                "inference.internal", 8080, ("192.168.1.20",)
            )
        )
        self.assertFalse(
            host_module._plain_http_allowed(
                "public.example", 80, ("93.184.216.34",)
            )
        )
        self.assertFalse(
            host_module._plain_http_allowed(
                "mixed.example", 80, ("192.168.1.20", "93.184.216.34")
            )
        )

        public_module = _load_module(
            "onyx-helper",
            {"EGRESS_ROUTE_CLASS": "public"},
        )
        self.assertFalse(
            public_module._plain_http_allowed("host.docker.internal", 3210)
        )

    async def test_plain_http_host_exception_bypasses_external_upstream(self) -> None:
        module = _load_module(
            "onyx-helper",
            {
                "EGRESS_ROUTE_CLASS": "host",
                "EGRESS_ALLOW_RFC1918": "true",
                "EGRESS_UPSTREAM_PROXY_URL": "socks5h://proxy.example:1080",
            },
        )
        direct = AsyncMock(return_value=(object(), object()))
        with (
            patch.object(module, "_open_validated_direct_connection", direct),
            patch.object(
                module,
                "_parse_proxy_url",
                side_effect=AssertionError("host exception reached external proxy"),
            ),
        ):
            await module._open_plain_http_forward_connection(
                "inference.internal", 8080, ("192.168.1.20",)
            )
        direct.assert_awaited_once_with(("192.168.1.20",), 8080)

    async def test_rfc1918_requires_host_route_and_explicit_opt_in(self) -> None:
        for route_class, enabled, allowed in (
            ("public", "true", False),
            ("host", "false", False),
            ("host", "true", True),
        ):
            with self.subTest(route_class=route_class, enabled=enabled):
                module = _load_module(
                    "onyx-helper",
                    {
                        "EGRESS_ROUTE_CLASS": route_class,
                        "EGRESS_ALLOW_RFC1918": enabled,
                    },
                )
                reason, addresses = await module._validate_destination(
                    "192.168.10.20", 443
                )
                self.assertEqual(reason is None, allowed)
                self.assertEqual(addresses, ("192.168.10.20",) if allowed else ())

    async def test_host_proxy_rejects_mixed_private_public_dns(self) -> None:
        module = _load_module(
            "onyx-helper",
            {
                "EGRESS_ROUTE_CLASS": "host",
                "EGRESS_ALLOW_RFC1918": "true",
            },
        )
        with patch.object(
            module,
            "_resolve_system_host",
            AsyncMock(return_value={"192.168.1.4", "93.184.216.34"}),
        ):
            reason, addresses = await module._validate_destination(
                "mixed.internal", 443
            )
        self.assertIn("mixed RFC1918", reason or "")
        self.assertEqual(addresses, ())

    def test_rfc1918_system_dns_names_are_suffix_limited(self) -> None:
        module = _load_module()
        for host in (
            "printer.local",
            "api.internal",
            "router.home.arpa",
            "ROUTER.HOME.ARPA.",
        ):
            with self.subTest(host=host):
                self.assertTrue(module._is_rfc1918_system_dns_name(host))
        for host in (
            "local",
            "internal",
            "home.arpa",
            "printer.local.example",
            "api.example",
        ):
            with self.subTest(host=host):
                self.assertFalse(module._is_rfc1918_system_dns_name(host))

    async def test_rfc1918_suffix_name_uses_system_dns_and_pins_private_set(
        self,
    ) -> None:
        module = _load_module(
            "onyx-helper",
            {
                "EGRESS_ROUTE_CLASS": "host",
                "EGRESS_ALLOW_RFC1918": "true",
            },
        )
        system_resolver = AsyncMock(
            return_value={"192.168.1.20", "192.168.1.21"}
        )
        myst_resolver = AsyncMock()
        with patch.object(
            module, "_resolve_system_host", system_resolver
        ), patch.object(module, "_resolve_target_host", myst_resolver):
            reason, addresses = await module._validate_destination(
                "inference.internal", 443
            )

        self.assertIsNone(reason)
        self.assertEqual(addresses, ("192.168.1.20", "192.168.1.21"))
        system_resolver.assert_awaited_once_with("inference.internal", 443)
        myst_resolver.assert_not_awaited()

    async def test_global_operator_local_answer_returns_to_selected_dns(self) -> None:
        module = _load_module(
            "onyx-helper",
            {
                "EGRESS_ROUTE_CLASS": "host",
                "EGRESS_ALLOW_RFC1918": "true",
            },
        )
        system_resolver = AsyncMock(return_value={"93.184.216.34"})
        myst_resolver = AsyncMock(return_value={"93.184.216.35"})
        with patch.object(
            module, "_resolve_system_host", system_resolver
        ), patch.object(module, "_resolve_target_host", myst_resolver):
            reason, addresses = await module._validate_destination(
                "public.internal", 443
            )

        self.assertIsNone(reason)
        self.assertEqual(addresses, ("93.184.216.35",))
        system_resolver.assert_awaited_once_with("public.internal", 443)
        myst_resolver.assert_awaited_once_with("public.internal", 443)

    async def test_rfc1918_enabled_does_not_system_resolve_arbitrary_name(
        self,
    ) -> None:
        module = _load_module(
            "onyx-helper",
            {
                "EGRESS_ROUTE_CLASS": "host",
                "EGRESS_ALLOW_RFC1918": "true",
            },
        )
        system_resolver = AsyncMock(return_value={"192.168.1.20"})
        myst_resolver = AsyncMock(return_value={"93.184.216.34"})
        with patch.object(
            module, "_resolve_system_host", system_resolver
        ), patch.object(module, "_resolve_target_host", myst_resolver):
            reason, addresses = await module._validate_destination(
                "private.example", 443
            )

        self.assertIsNone(reason)
        self.assertEqual(addresses, ("93.184.216.34",))
        system_resolver.assert_not_awaited()
        myst_resolver.assert_awaited_once_with("private.example", 443)

    async def test_rfc1918_disabled_does_not_system_resolve_local_suffix(
        self,
    ) -> None:
        module = _load_module(
            "onyx-helper",
            {
                "EGRESS_ROUTE_CLASS": "host",
                "EGRESS_ALLOW_RFC1918": "false",
            },
        )
        system_resolver = AsyncMock()
        myst_resolver = AsyncMock(return_value={"93.184.216.34"})
        with patch.object(
            module, "_resolve_system_host", system_resolver
        ), patch.object(module, "_resolve_target_host", myst_resolver):
            reason, addresses = await module._validate_destination(
                "inference.internal", 443
            )

        self.assertIsNone(reason)
        self.assertEqual(addresses, ("93.184.216.34",))
        system_resolver.assert_not_awaited()
        myst_resolver.assert_awaited_once_with("inference.internal", 443)

    def test_browser_policy_allows_search_hosts(self) -> None:
        module = _load_module("browser")
        self.assertFalse(module.BLOCK_SEARCH_ENGINES)
        self.assertTrue(module._is_search_engine("www.google.com"))

    def test_onyx_helper_policy_allows_search_hosts(self) -> None:
        module = _load_module(
            "onyx-helper",
            {
                "PREFETCH_PROXY_HOST": "127.0.0.1",
                "EGRESS_PROXY_ALLOWED_CLIENT_HOSTS": "",
            },
        )
        self.assertFalse(module.BLOCK_SEARCH_ENGINES)
        self.assertTrue(module._is_search_engine("www.google.com"))
        self.assertTrue(module._listener_is_loopback_only())

    def test_wildcard_listener_is_not_loopback_only(self) -> None:
        module = _load_module(
            "onyx-helper",
            {
                "PREFETCH_PROXY_HOST": "0.0.0.0",
                "EGRESS_PROXY_ALLOWED_CLIENT_HOSTS": "",
            },
        )
        self.assertFalse(module._listener_is_loopback_only())

    async def test_helper_trusted_internal_destination_bypasses_upstream_exactly(
        self,
    ) -> None:
        module = _load_module(
            "onyx-helper",
            {
                "PREFETCH_PROXY_HOST": "127.0.0.1",
                "EGRESS_PROXY_ALLOWED_CLIENT_HOSTS": "",
                "EGRESS_ROUTE_CLASS": "host",
                "EGRESS_PROXY_TRUSTED_INTERNAL_DESTINATIONS": "localhost:8091",
                "EGRESS_UPSTREAM_PROXY_URL": "socks5h://proxy.example:1080",
            },
        )
        resolve = AsyncMock(return_value={"127.0.0.1"})
        fake_reader = object()
        fake_writer = object()
        open_connection = AsyncMock(return_value=(fake_reader, fake_writer))

        with patch.object(module, "_resolve_system_host", resolve), patch.object(
            module.asyncio, "open_connection", open_connection
        ):
            reason, addresses = await module._validate_destination("localhost", 8091)
            result = await module._connect_via_upstream(
                "localhost", 8091, addresses
            )
            plain_result = await module._open_plain_http_forward_connection(
                "localhost", 8091, addresses
            )

        self.assertIsNone(reason)
        self.assertEqual(addresses, ("127.0.0.1",))
        self.assertEqual(result, (fake_reader, fake_writer))
        self.assertEqual(plain_result, (fake_reader, fake_writer, False))
        resolve.assert_awaited_once_with("localhost", 8091)
        self.assertEqual(open_connection.await_count, 2)
        open_connection.assert_awaited_with("127.0.0.1", 8091)
        self.assertFalse(module._is_trusted_internal_destination("localhost", 8080))

    def test_trusted_internal_destination_rejected_for_non_helper_policy(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "allowed only"):
            _load_module(
                "browser",
                {
                    "EGRESS_PROXY_TRUSTED_INTERNAL_DESTINATIONS": "localhost:8091"
                },
            )

    def test_trusted_internal_destination_rejected_for_public_route(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "host route class"):
            _load_module(
                "onyx-helper",
                {
                    "EGRESS_ROUTE_CLASS": "public",
                    "EGRESS_PROXY_TRUSTED_INTERNAL_DESTINATIONS": (
                        "doc-drop-web:8091"
                    ),
                },
            )

    def test_search_policy_normalizes_fully_qualified_hostnames(self) -> None:
        module = _load_module("executor")
        self.assertTrue(module._is_search_engine("google.com."))
        self.assertTrue(module._is_search_engine("WWW.GOOGLE.COM."))

    def test_default_docker_internal_names_are_blocked(self) -> None:
        module = _load_module()
        for host in (
            "host.docker.internal",
            "gateway.docker.internal",
            "vm.docker.internal",
            "kubernetes.docker.internal",
            "subdomain.gateway.docker.internal",
            "docker.for.mac.host.internal",
            "docker.for.mac.localhost",
            "docker.for.mac.gateway.internal",
            "docker.for.win.host.internal",
            "docker.for.win.localhost",
        ):
            with self.subTest(host=host):
                self.assertIsNotNone(module._hostname_block_reason(host))

    def test_idna_equivalent_docker_internal_name_is_blocked(self) -> None:
        module = _load_module()
        self.assertIsNotNone(
            module._hostname_block_reason(
                "HOST\N{IDEOGRAPHIC FULL STOP}"
                "DOCKER\N{IDEOGRAPHIC FULL STOP}INTERNAL"
            )
        )

    def test_compose_style_service_and_container_names_are_blocked(self) -> None:
        module = _load_module()
        for host in (
            "api_server",
            "crw-service-gateway",
            "onyx-api_server-1",
            "executor-egress-bridge",
        ):
            with self.subTest(host=host):
                self.assertEqual(
                    module._hostname_block_reason(host),
                    "single-label/internal hostname",
                )

    def test_config_cannot_remove_builtin_internal_names(self) -> None:
        module = _load_module(
            env_overrides={"PREFETCH_BLOCK_INTERNAL_HOSTS": "custom.internal"}
        )
        self.assertIn("host.docker.internal", module.BLOCKED_HOSTNAMES)
        self.assertIn("custom.internal", module.BLOCKED_HOSTNAMES)

    def test_upstream_proxy_logging_redacts_credentials(self) -> None:
        module = _load_module(
            env_overrides={
                "EGRESS_UPSTREAM_PROXY_URL": "https://user:secret@proxy.example:8443"
            }
        )
        module._validate_upstream_proxy_config()
        self.assertEqual(
            module._sanitized_upstream_proxy(), "https://proxy.example:8443"
        )
        self.assertNotIn("secret", module._sanitized_upstream_proxy())

    def test_invalid_upstream_proxy_fails_validation(self) -> None:
        module = _load_module(
            env_overrides={"EGRESS_UPSTREAM_PROXY_URL": "ftp://proxy.example"}
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

    async def test_request_body_streaming_has_no_proxy_deadline(self) -> None:
        module = _load_module()
        encoded = b"5\r\nhello\r\n0\r\n\r\n"
        reader = module.asyncio.StreamReader()
        reader.feed_data(encoded)
        reader.feed_eof()
        writer = self._Writer()

        with patch.object(
            module.asyncio,
            "wait_for",
            side_effect=AssertionError("body streaming installed a deadline"),
        ):
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
            env_overrides={"EGRESS_ALLOW_HTTP_URLS": "true"}
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
            module,
            "_resolve_system_host",
            AsyncMock(return_value={"172.30.0.5"}),
        ):
            self.assertIsNone(
                await module._allowed_client_reason(("172.30.0.5", 12345))
            )
            self.assertIn(
                "not an allowed bridge",
                await module._allowed_client_reason(("172.30.0.6", 12345)) or "",
            )

    async def test_direct_mode_uses_myst_provider_dns(self) -> None:
        module = _load_module()
        vpn_query = AsyncMock(return_value={"93.184.216.34"})
        system_query = AsyncMock()
        with patch.object(
            module,
            "_myst_provider_dns_endpoint",
            return_value=("10.10.0.1", "10.10.0.2"),
        ), patch.object(module, "_dns_query_a", vpn_query), patch.object(
            module, "_resolve_system_host", system_query
        ):
            addresses = await module._resolve_target_host("example.com", 443)

        self.assertEqual(addresses, {"93.184.216.34"})
        vpn_query.assert_awaited_once_with(
            "example.com", "10.10.0.1", "10.10.0.2"
        )
        system_query.assert_not_awaited()

    def test_provider_dns_is_first_usable_myst_subnet_address(self) -> None:
        module = _load_module()
        with patch.object(
            module,
            "_interface_ipv4_network",
            return_value=(
                ipaddress.IPv4Address("10.42.7.2"),
                ipaddress.IPv4Network("10.42.7.0/24"),
            ),
        ):
            self.assertEqual(module._myst_provider_dns_ip(), "10.42.7.1")

    def test_provider_dns_socket_is_source_bound_to_myst_address(self) -> None:
        module = _load_module()
        sock = Mock()

        module._bind_socket_to_vpn_address(sock, "10.42.7.2")

        sock.bind.assert_called_once_with(("10.42.7.2", 0))

    def test_provider_dns_socket_bind_failure_is_fatal(self) -> None:
        module = _load_module()
        sock = Mock()
        sock.bind.side_effect = OSError("not available")

        with self.assertRaisesRegex(RuntimeError, "cannot source-bind provider DNS"):
            module._bind_socket_to_vpn_address(sock, "10.42.7.2")

    async def test_explicit_no_vpn_mode_uses_system_dns(self) -> None:
        module = _load_module(env_overrides={"MYST_VPN_ENABLED": "false"})
        system_query = AsyncMock(return_value={"93.184.216.34"})
        with patch.object(module, "_resolve_system_host", system_query), patch.object(
            module, "_dns_query_a", AsyncMock()
        ) as vpn_query:
            addresses = await module._resolve_target_host("example.com", 443)

        self.assertEqual(addresses, {"93.184.216.34"})
        system_query.assert_awaited_once_with("example.com", 443)
        vpn_query.assert_not_awaited()

    def test_invalid_vpn_mode_fails_startup(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must be exactly"):
            _load_module(env_overrides={"MYST_VPN_ENABLED": "flase"})

    async def test_every_upstream_proxy_scheme_receives_target_hostname(self) -> None:
        for scheme in ("http", "https", "socks5", "socks5h"):
            with self.subTest(scheme=scheme):
                module = _load_module(
                    env_overrides={
                        "EGRESS_UPSTREAM_PROXY_URL": (
                            f"{scheme}://proxy.example:1080"
                        ),
                        "EGRESS_ROUTE_CLASS": "host",
                        "EGRESS_ALLOW_RFC1918": "true",
                    }
                )
                target_resolver = AsyncMock()
                system_resolver = AsyncMock()
                with patch.object(
                    module, "_resolve_target_host", target_resolver
                ), patch.object(module, "_resolve_system_host", system_resolver):
                    reason, addresses = await module._validate_destination(
                        "target.example", 443
                    )

                self.assertIsNone(reason)
                self.assertEqual(addresses, ())
                target_resolver.assert_not_awaited()
                system_resolver.assert_not_awaited()

    async def test_both_socks_schemes_send_domain_name_to_proxy(self) -> None:
        target = "target.example"
        expected_request = (
            b"\x05\x01\x00\x03"
            + bytes([len(target)])
            + target.encode()
            + (443).to_bytes(2, "big")
        )
        for scheme in ("socks5", "socks5h"):
            with self.subTest(scheme=scheme):
                module = _load_module()
                reader = module.asyncio.StreamReader()
                reader.feed_data(
                    b"\x05\x00"  # no-auth greeting response
                    b"\x05\x00\x00\x01"  # successful IPv4 connect response
                    b"\x00\x00\x00\x00\x00\x00"  # bound address and port
                )
                reader.feed_eof()
                writer = self._Writer()
                with patch.object(
                    module,
                    "_open_http_proxy_connection",
                    AsyncMock(return_value=(reader, writer)),
                ):
                    await module._connect_via_socks5(
                        "proxy.example",
                        1080,
                        target,
                        443,
                        None,
                        None,
                        scheme,
                    )

                self.assertTrue(writer.data.startswith(b"\x05\x01\x00"))
                self.assertIn(expected_request, writer.data)

    async def test_public_upstream_proxy_bootstrap_uses_myst_dns(self) -> None:
        module = _load_module(
            env_overrides={
                "EGRESS_UPSTREAM_PROXY_URL": "http://proxy.example:8080"
            }
        )
        vpn_resolver = AsyncMock(return_value={"93.184.216.34"})
        open_connection = AsyncMock(return_value=(object(), object()))
        with patch.object(module, "_resolve_target_host", vpn_resolver), patch.object(
            module.asyncio, "open_connection", open_connection
        ):
            await module._open_http_proxy_connection(
                "proxy.example", 8080, "http"
            )

        vpn_resolver.assert_awaited_once_with("proxy.example", 8080)
        open_connection.assert_awaited_once_with(
            "93.184.216.34", 8080, ssl=None, server_hostname=None
        )

    async def test_no_vpn_upstream_proxy_bootstrap_uses_system_dns(self) -> None:
        module = _load_module(
            env_overrides={
                "MYST_VPN_ENABLED": "false",
                "EGRESS_UPSTREAM_PROXY_URL": "http://proxy.example:8080",
            }
        )
        system_resolver = AsyncMock(return_value={"93.184.216.34"})
        vpn_resolver = AsyncMock()
        open_connection = AsyncMock(return_value=(object(), object()))
        with patch.object(
            module, "_resolve_system_host", system_resolver
        ), patch.object(
            module, "_resolve_target_host", vpn_resolver
        ), patch.object(module.asyncio, "open_connection", open_connection):
            await module._open_http_proxy_connection(
                "proxy.example", 8080, "http"
            )

        system_resolver.assert_awaited_once_with("proxy.example", 8080)
        vpn_resolver.assert_not_awaited()
        open_connection.assert_awaited_once_with(
            "93.184.216.34", 8080, ssl=None, server_hostname=None
        )

    async def test_public_proxy_name_still_uses_myst_dns_with_rfc1918_enabled(
        self,
    ) -> None:
        module = _load_module(
            env_overrides={
                "EGRESS_UPSTREAM_PROXY_URL": "socks5h://proxy.example:1080",
                "EGRESS_ALLOW_RFC1918": "true",
            }
        )
        vpn_resolver = AsyncMock(return_value={"93.184.216.34"})
        system_resolver = AsyncMock()
        open_connection = AsyncMock(return_value=(object(), object()))
        with patch.object(
            module, "_resolve_target_host", vpn_resolver
        ), patch.object(
            module, "_resolve_system_host", system_resolver
        ), patch.object(module.asyncio, "open_connection", open_connection):
            await module._open_http_proxy_connection(
                "proxy.example", 1080, "socks5h"
            )

        vpn_resolver.assert_awaited_once_with("proxy.example", 1080)
        system_resolver.assert_not_awaited()
        open_connection.assert_awaited_once_with(
            "93.184.216.34", 1080, ssl=None, server_hostname=None
        )

    async def test_global_literal_proxy_uses_namespace_route_without_dns(
        self,
    ) -> None:
        module = _load_module(
            env_overrides={
                "EGRESS_UPSTREAM_PROXY_URL": "socks5h://93.184.216.34:1080"
            }
        )
        vpn_resolver = AsyncMock()
        system_resolver = AsyncMock()
        open_connection = AsyncMock(return_value=(object(), object()))
        with patch.object(
            module, "_resolve_target_host", vpn_resolver
        ), patch.object(
            module, "_resolve_system_host", system_resolver
        ), patch.object(module.asyncio, "open_connection", open_connection):
            await module._open_http_proxy_connection(
                "93.184.216.34", 1080, "socks5h"
            )

        vpn_resolver.assert_not_awaited()
        system_resolver.assert_not_awaited()
        open_connection.assert_awaited_once_with(
            "93.184.216.34", 1080, ssl=None, server_hostname=None
        )

    async def test_internal_upstream_proxy_bootstrap_uses_system_dns(self) -> None:
        module = _load_module(
            env_overrides={
                "EGRESS_UPSTREAM_PROXY_URL": (
                    "socks5h://host.docker.internal:9150"
                )
            }
        )
        system_resolver = AsyncMock(return_value={"192.168.65.2"})
        vpn_resolver = AsyncMock()
        open_connection = AsyncMock(return_value=(object(), object()))
        with patch.object(
            module, "_resolve_system_host", system_resolver
        ), patch.object(module, "_resolve_target_host", vpn_resolver), patch.object(
            module.asyncio, "open_connection", open_connection
        ):
            await module._open_http_proxy_connection(
                "host.docker.internal", 9150, "socks5h"
            )

        system_resolver.assert_awaited_once_with("host.docker.internal", 9150)
        vpn_resolver.assert_not_awaited()

    async def test_unknown_internal_proxy_name_never_reaches_system_dns(self) -> None:
        module = _load_module()
        system_resolver = AsyncMock()
        vpn_resolver = AsyncMock()
        with patch.object(
            module, "_resolve_system_host", system_resolver
        ), patch.object(module, "_resolve_target_host", vpn_resolver):
            with self.assertRaisesRegex(
                ConnectionError, "upstream proxy hostname requires"
            ):
                await module._open_http_proxy_connection(
                    "unknown-proxy", 1080, "socks5"
                )

        system_resolver.assert_not_awaited()
        vpn_resolver.assert_not_awaited()

    async def test_operator_local_proxy_name_requires_all_rfc1918_answers(
        self,
    ) -> None:
        module = _load_module(
            env_overrides={"EGRESS_ALLOW_RFC1918": "true"}
        )
        system_resolver = AsyncMock(return_value={"192.168.1.20"})
        vpn_resolver = AsyncMock()
        open_connection = AsyncMock(return_value=(object(), object()))
        with patch.object(
            module, "_resolve_system_host", system_resolver
        ), patch.object(module, "_resolve_target_host", vpn_resolver), patch.object(
            module.asyncio, "open_connection", open_connection
        ):
            await module._open_http_proxy_connection(
                "proxy.internal", 1080, "socks5"
            )

        system_resolver.assert_awaited_once_with("proxy.internal", 1080)
        vpn_resolver.assert_not_awaited()
        open_connection.assert_awaited_once_with(
            "192.168.1.20", 1080, ssl=None, server_hostname=None
        )

    async def test_direct_readiness_checks_vpn_dns_without_public_connection(self) -> None:
        module = _load_module()
        writer = self._Writer()
        open_connection = AsyncMock(return_value=(object(), writer))
        resolve = AsyncMock(return_value={"93.184.216.34"})
        with patch.object(
            module.asyncio, "open_connection", open_connection
        ), patch.object(module, "_resolve_target_host", resolve):
            await module._check_egress_readiness()

        open_connection.assert_awaited_once_with("127.0.0.1", module.LISTEN_PORT)
        resolve.assert_awaited_once_with("example.com", 443)

    async def test_upstream_readiness_does_not_resolve_browsing_target(self) -> None:
        module = _load_module(
            env_overrides={
                "EGRESS_UPSTREAM_PROXY_URL": (
                    "https://user:secret@proxy.example:8443"
                )
            }
        )
        listener_writer = self._Writer()
        probe = AsyncMock()
        target_resolver = AsyncMock()
        with patch.object(
            module.asyncio,
            "open_connection",
            AsyncMock(return_value=(object(), listener_writer)),
        ), patch.object(module, "_probe_http_proxy_endpoint", probe), patch.object(
            module, "_resolve_target_host", target_resolver
        ):
            await module._check_egress_readiness()

        probe.assert_awaited_once_with(
            "proxy.example", 8443, "user", "secret", "https"
        )
        target_resolver.assert_not_awaited()

    async def test_http_proxy_readiness_rejects_authentication_failure(self) -> None:
        module = _load_module()
        reader = module.asyncio.StreamReader()
        reader.feed_data(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")
        reader.feed_eof()
        writer = self._Writer()
        with patch.object(
            module,
            "_open_http_proxy_connection",
            AsyncMock(return_value=(reader, writer)),
        ):
            with self.assertRaisesRegex(ConnectionError, "authentication failed"):
                await module._probe_http_proxy_endpoint(
                    "proxy.example", 8080, "user", "secret", "http"
                )

        self.assertIn(b"OPTIONS * HTTP/1.1\r\n", writer.data)
        self.assertIn(b"Proxy-Authorization: Basic ", writer.data)
        self.assertNotIn(b"secret", writer.data)

    def test_dns_a_response_parser(self) -> None:
        module = _load_module()
        query_id = 0x1234
        question = module._encode_dns_name("example.com") + struct.pack("!HH", 1, 1)
        answer = (
            b"\xc0\x0c"
            + struct.pack("!HHIH", 1, 1, 60, 4)
            + bytes([93, 184, 216, 34])
        )
        response = struct.pack("!HHHHHH", query_id, 0x8180, 1, 1, 0, 0)
        response += question + answer

        addresses, truncated = module._parse_dns_a_response(response, query_id)

        self.assertEqual(addresses, {"93.184.216.34"})
        self.assertFalse(truncated)

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
