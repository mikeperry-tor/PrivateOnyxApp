from __future__ import annotations

import io
import unittest
from collections import defaultdict, deque
from contextlib import redirect_stdout
from decimal import Decimal
from unittest.mock import patch

from myst import vpn_cli


IDENTITY = "0x1111111111111111111111111111111111111111"
CHANNEL = "0x2222222222222222222222222222222222222222"


def result(stdout: str = "", returncode: int = 0, stderr: str = "") -> vpn_cli.CommandResult:
    return vpn_cli.CommandResult(returncode, stdout, stderr)


def details(status: str = "registered", balance: str = "0") -> str:
    return (
        f"Registration Status: {status}\n"
        f"Channel address: {CHANNEL}\n"
        f"Balance: {balance} MYST\n"
    )


def order_list(*entries: tuple[str, str]) -> str:
    if not entries:
        return "[INFO] No orders found\n"
    return "".join(
        f"[INFO] Order ID '{order_id}' is in state: '{state}'\n"
        for order_id, state in entries
    )


def order_detail(order_id: str, state: str = "new", url: str = "https://pay.example/order") -> str:
    return (
        order_list((order_id, state))
        + "[INFO] Pay: 1 MYST\n"
        + f'[INFO] Data: {{"payment_url":"{url}"}}\n'
    )


GATEWAYS = (
    "[INFO] Gateway:coingate\n"
    "[INFO] Suggested minimum order:71\n"
    "[INFO] Supported currencies:BTC, MYST\n"
)


class FakeMyst:
    def __init__(self) -> None:
        self.responses: dict[tuple[str, ...], deque[vpn_cli.CommandResult]] = defaultdict(deque)
        self.calls: list[tuple[str, ...]] = []

    def add(self, command: tuple[str, ...], *responses: vpn_cli.CommandResult) -> None:
        self.responses[command].extend(responses)

    def _take(self, command: tuple[str, ...]) -> vpn_cli.CommandResult:
        self.calls.append(command)
        queue = self.responses[command]
        if not queue:
            raise AssertionError(f"unexpected command: {command}")
        return queue.popleft()

    def run(self, *args: str) -> vpn_cli.CommandResult:
        return self._take(("run", *args))

    def cli(self, *args: str) -> vpn_cli.CommandResult:
        return self._take(("cli", *args))


def configure_prepared_identity(fake: FakeMyst, *, status: str = "registered", balance: str = "0") -> None:
    fake.add(("run", "connection", "info"), result("ok"))
    fake.add(("cli", "identities", "list"), result(IDENTITY))
    fake.add(("cli", "identities", "unlock", IDENTITY), result("unlocked"))
    fake.add(("cli", "identities", "balance", IDENTITY), result(f"Balance: {balance} MYST"))
    fake.add(("cli", "identities", "get", IDENTITY), result(details(status, balance)))


class MystVpnCliTests(unittest.TestCase):
    def test_identity_listing_failure_never_creates_identity(self) -> None:
        for failed in (
            result(returncode=1, stderr="remote failed"),
            result("[ERROR] remote failed"),
        ):
            fake = FakeMyst()
            fake.add(("cli", "identities", "list"), failed)
            with self.assertRaisesRegex(vpn_cli.WorkflowError, "identity listing failed"):
                vpn_cli.select_identity(fake, None)
            self.assertNotIn(("cli", "identities", "new"), fake.calls)

    def test_identity_listing_ignores_addresses_embedded_in_diagnostics(self) -> None:
        fake = FakeMyst()
        fake.add(
            ("cli", "identities", "list"),
            result(f"warning: remote peer {IDENTITY} unavailable\n"),
            result(f"[+] {IDENTITY}\n"),
        )
        fake.add(("cli", "identities", "new"), result("created"))
        self.assertEqual(vpn_cli.select_identity(fake, None), IDENTITY)
        self.assertEqual(fake.calls.count(("cli", "identities", "new")), 1)

    def test_successful_empty_listing_creates_exactly_one_identity(self) -> None:
        fake = FakeMyst()
        fake.add(("cli", "identities", "list"), result(""), result(IDENTITY))
        fake.add(("cli", "identities", "new"), result("created"))
        self.assertEqual(vpn_cli.select_identity(fake, None), IDENTITY)
        self.assertEqual(fake.calls.count(("cli", "identities", "new")), 1)

    def test_multiple_identities_require_explicit_selection(self) -> None:
        other = "0x3333333333333333333333333333333333333333"
        fake = FakeMyst()
        fake.add(("cli", "identities", "list"), result(f"{IDENTITY}\n{other}\n"))
        with self.assertRaisesRegex(vpn_cli.WorkflowError, "multiple Myst identities"):
            vpn_cli.select_identity(fake, None)
        fake = FakeMyst()
        fake.add(("cli", "identities", "list"), result(f"{IDENTITY}\n{other}\n"))
        self.assertEqual(vpn_cli.select_identity(fake, other), other)

    def test_registration_targets_exact_identity_once_and_does_not_retry(self) -> None:
        fake = FakeMyst()
        fake.add(
            ("cli", "identities", "get", IDENTITY),
            result(details("unregistered")),
            result(details("inprogress")),
        )
        fake.add(("cli", "identities", "register", IDENTITY), result("started"))
        observed = vpn_cli.ensure_registration(fake, IDENTITY, sleeper=lambda _: None)
        self.assertEqual(observed.registration, "inprogress")
        self.assertEqual(fake.calls.count(("cli", "identities", "register", IDENTITY)), 1)
        self.assertFalse(any(call[:2] == ("run", "account") for call in fake.calls))

    def test_registration_failure_is_visible_and_not_retried(self) -> None:
        fake = FakeMyst()
        fake.add(("cli", "identities", "get", IDENTITY), result(details("unregistered")))
        fake.add(
            ("cli", "identities", "register", IDENTITY),
            result(returncode=1, stderr="registration unavailable"),
        )
        with self.assertRaisesRegex(vpn_cli.WorkflowError, "registration request"):
            vpn_cli.ensure_registration(fake, IDENTITY)
        self.assertEqual(fake.calls.count(("cli", "identities", "register", IDENTITY)), 1)

    def test_order_listing_failure_never_creates_order(self) -> None:
        fake = FakeMyst()
        fake.add(
            ("cli", "orders", "get-all", IDENTITY),
            result(returncode=1, stderr="remote unavailable"),
        )
        with self.assertRaisesRegex(vpn_cli.WorkflowError, "listing orders"):
            vpn_cli.list_orders(fake, IDENTITY)
        self.assertFalse(any("create" in call for call in fake.calls))

    def test_order_parser_rejects_unknown_state_and_unrecognized_success(self) -> None:
        with self.assertRaisesRegex(vpn_cli.WorkflowError, "unknown order state"):
            vpn_cli.parse_orders(order_list(("o1", "pending")))
        with self.assertRaisesRegex(vpn_cli.WorkflowError, "unsafe or malformed"):
            vpn_cli.parse_orders(order_list(("unsafe\x1b]8;;https://example.test\x07", "new")))
        with self.assertRaisesRegex(vpn_cli.WorkflowError, "unrecognized"):
            vpn_cli.parse_orders("[ERROR] could not get orders")

    def test_payment_url_requires_one_known_https_field(self) -> None:
        self.assertEqual(
            vpn_cli.extract_payment_url('[INFO] Data: {"payment_url":"https://pay.example/x"}'),
            "https://pay.example/x",
        )
        for payload in (
            '[INFO] Data: {"payment_url":"http://pay.example/x"}',
            '[INFO] Data: {"unrelated":"https://evil.example/x"}',
            '[INFO] Data: {"payment_url":"https://a/x","url":"https://b/x"}',
            '[INFO] Data: {"payment_url":"https://pay.example/x\\u0007bad"}',
            '[INFO] Data: {"payment_url":"https://[not-ipv6/x"}',
            "[INFO] Data: not-json",
        ):
            with self.assertRaises(vpn_cli.WorkflowError):
                vpn_cli.extract_payment_url(payload)

    def test_order_configuration_is_strict_and_checks_live_gateway(self) -> None:
        base = {
            "MYST_VPN_ORDER_AMOUNT": "100",
            "MYST_VPN_ORDER_CURRENCY": "MYST",
            "MYST_VPN_ORDER_GATEWAY": "coingate",
            "MYST_VPN_ORDER_COUNTRY": "US",
            "MYST_VPN_ORDER_GATEWAY_DATA": "custom_id=test",
        }
        fake = FakeMyst()
        fake.add(("cli", "orders", "gateways"), result(GATEWAYS))
        with patch.dict("os.environ", base, clear=False):
            self.assertEqual(
                vpn_cli.validate_order_configuration(fake),
                ("100", "MYST", "coingate", "US", "custom_id=test"),
            )
        for key, value, message in (
            ("MYST_VPN_ORDER_AMOUNT", "NaN", "finite decimal"),
            ("MYST_VPN_ORDER_AMOUNT", "71", "greater than"),
            ("MYST_VPN_ORDER_CURRENCY", "USD", "not supported"),
            ("MYST_VPN_ORDER_COUNTRY", "USA", "two-letter"),
            ("MYST_VPN_ORDER_GATEWAY_DATA", "broken", "key=value"),
            ("MYST_VPN_ORDER_GATEWAY", "coin gate", "malformed"),
            ("MYST_VPN_ORDER_GATEWAY_DATA", "custom id=test", "invalid key"),
            ("MYST_VPN_ORDER_GATEWAY_DATA", "custom_id=", "invalid key"),
        ):
            fake = FakeMyst()
            fake.add(("cli", "orders", "gateways"), result(GATEWAYS))
            invalid = dict(base)
            invalid[key] = value
            with patch.dict("os.environ", invalid, clear=False):
                with self.assertRaisesRegex(vpn_cli.WorkflowError, message):
                    vpn_cli.validate_order_configuration(fake)

        fake = FakeMyst()
        fake.add(
            ("cli", "orders", "gateways"),
            result(GATEWAYS.replace("Suggested minimum order:71", "Suggested minimum order:NaN")),
        )
        with patch.dict("os.environ", base, clear=False):
            with self.assertRaisesRegex(vpn_cli.WorkflowError, "malformed metadata"):
                vpn_cli.validate_order_configuration(fake)

        fake = FakeMyst()
        fake.add(("cli", "orders", "gateways"), result(GATEWAYS + GATEWAYS))
        with patch.dict("os.environ", base, clear=False):
            with self.assertRaisesRegex(vpn_cli.WorkflowError, "duplicate"):
                vpn_cli.validate_order_configuration(fake)

    def test_zero_exit_without_created_order_fails_postcondition(self) -> None:
        fake = FakeMyst()
        fake.add(("cli", "orders", "gateways"), result(GATEWAYS))
        fake.add(
            ("cli", "orders", "create", IDENTITY, "100", "MYST", "coingate", "US", "custom_id=test"),
            result("[ERROR] no such gateway"),
        )
        fake.add(("cli", "orders", "get-all", IDENTITY), result(order_list()))
        env = {
            "MYST_VPN_ORDER_AMOUNT": "100",
            "MYST_VPN_ORDER_CURRENCY": "MYST",
            "MYST_VPN_ORDER_GATEWAY": "coingate",
            "MYST_VPN_ORDER_COUNTRY": "US",
            "MYST_VPN_ORDER_GATEWAY_DATA": "custom_id=test",
        }
        with patch.dict("os.environ", env, clear=False):
            with self.assertRaisesRegex(vpn_cli.WorkflowError, "postcondition is ambiguous"):
                vpn_cli.create_order(fake, IDENTITY, [])
        self.assertEqual(fake.calls.count(("cli", "orders", "create", IDENTITY, "100", "MYST", "coingate", "US", "custom_id=test")), 1)

    def test_create_output_cannot_misrepresent_an_old_order_as_new(self) -> None:
        fake = FakeMyst()
        fake.add(("cli", "orders", "gateways"), result(GATEWAYS))
        fake.add(
            ("cli", "orders", "create", IDENTITY, "100", "MYST", "coingate", "US", "custom_id=test"),
            result(order_list(("old", "new"))),
        )
        fake.add(
            ("cli", "orders", "get-all", IDENTITY),
            result(order_list(("old", "new"))),
        )
        env = {
            "MYST_VPN_ORDER_AMOUNT": "100",
            "MYST_VPN_ORDER_CURRENCY": "MYST",
            "MYST_VPN_ORDER_GATEWAY": "coingate",
            "MYST_VPN_ORDER_COUNTRY": "US",
            "MYST_VPN_ORDER_GATEWAY_DATA": "custom_id=test",
        }
        with patch.dict("os.environ", env, clear=False):
            with self.assertRaisesRegex(vpn_cli.WorkflowError, "postcondition is ambiguous"):
                vpn_cli.create_order(
                    fake,
                    IDENTITY,
                    [vpn_cli.OrderSummary("old", "new")],
                )

    def test_failed_create_response_reconciles_one_exact_new_order(self) -> None:
        fake = FakeMyst()
        fake.add(("cli", "orders", "gateways"), result(GATEWAYS))
        fake.add(
            ("cli", "orders", "create", IDENTITY, "100", "MYST", "coingate", "US", "custom_id=test"),
            result(returncode=1, stderr="response lost"),
        )
        fake.add(("cli", "orders", "get-all", IDENTITY), result(order_list(("o1", "new"))))
        fake.add(("cli", "orders", "get", IDENTITY, "o1"), result(order_detail("o1")))
        env = {
            "MYST_VPN_ORDER_AMOUNT": "100",
            "MYST_VPN_ORDER_CURRENCY": "MYST",
            "MYST_VPN_ORDER_GATEWAY": "coingate",
            "MYST_VPN_ORDER_COUNTRY": "US",
            "MYST_VPN_ORDER_GATEWAY_DATA": "custom_id=test",
        }
        with patch.dict("os.environ", env, clear=False), redirect_stdout(io.StringIO()) as output:
            vpn_cli.create_order(fake, IDENTITY, [])
        self.assertIn("exact-order verification", output.getvalue())
        self.assertIn("PAYMENT URL: https://pay.example/order", output.getvalue())

    def test_long_payment_transitions_never_create_duplicate_order(self) -> None:
        # Hours or days passing between commands is represented by independent,
        # authoritative remote snapshots; no local timer/state is trusted.
        pending = FakeMyst()
        configure_prepared_identity(pending)
        pending.add(("cli", "identities", "get", IDENTITY), result(details("registered", "0")))
        pending.add(("cli", "identities", "balance", IDENTITY), result("Balance: 0 MYST"))
        pending.add(("cli", "identities", "get", IDENTITY), result(details("registered", "0")))
        pending.add(("cli", "orders", "get-all", IDENTITY), result(order_list(("o1", "new"))))
        pending.add(("cli", "orders", "get", IDENTITY, "o1"), result(order_detail("o1")))
        with redirect_stdout(io.StringIO()):
            vpn_cli.cmd_signup(pending)
        self.assertFalse(any("create" in call for call in pending.calls))

        paid = FakeMyst()
        configure_prepared_identity(paid)
        paid.add(("cli", "identities", "get", IDENTITY), result(details("registered", "0")))
        paid.add(("cli", "identities", "balance", IDENTITY), result("Balance: 0 MYST"))
        paid.add(("cli", "identities", "get", IDENTITY), result(details("registered", "0")))
        paid.add(("cli", "orders", "get-all", IDENTITY), result(order_list(("o1", "paid"))))
        with self.assertRaisesRegex(vpn_cli.WorkflowError, "settlement"):
            vpn_cli.cmd_signup(paid)
        self.assertFalse(any("create" in call for call in paid.calls))

        funded = FakeMyst()
        configure_prepared_identity(funded, balance="25")
        with redirect_stdout(io.StringIO()):
            vpn_cli.cmd_signup(funded)
        self.assertFalse(any("orders" in call for call in funded.calls))

    def test_orderstatus_displays_urls_only_for_incomplete_orders(self) -> None:
        fake = FakeMyst()
        configure_prepared_identity(fake)
        fake.add(
            ("cli", "orders", "get-all", IDENTITY),
            result(order_list(("new1", "new"), ("paid1", "paid"), ("failed1", "failed"))),
        )
        fake.add(("cli", "orders", "get", IDENTITY, "new1"), result(order_detail("new1")))
        with redirect_stdout(io.StringIO()) as output:
            vpn_cli.cmd_orderstatus(fake)
        self.assertIn("PAYMENT URL", output.getvalue())
        self.assertNotIn(("cli", "orders", "get", IDENTITY, "paid1"), fake.calls)
        self.assertNotIn(("cli", "orders", "get", IDENTITY, "failed1"), fake.calls)


if __name__ == "__main__":
    unittest.main()
