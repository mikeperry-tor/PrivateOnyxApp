#!/usr/bin/env python3
"""Fail-closed Myst identity, registration, and funding workflow."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Callable, Iterable, Sequence
from urllib.parse import urlsplit


IDENTITY_RE = re.compile(r"0x[0-9a-fA-F]{40}")
IDENTITY_LINE_RE = re.compile(
    r"^\s*(?:\[\+\]\s*)?(0x[0-9a-fA-F]{40})\s*$", re.MULTILINE
)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
ORDER_RE = re.compile(r"Order ID '([^']+)' is in state: '([^']+)'")
ORDER_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,200}")
GATEWAY_NAME_RE = re.compile(r"[A-Za-z0-9._:-]{1,100}")
GATEWAY_DATA_KEY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
CLI_ERROR_RE = re.compile(r"^\s*\[ERROR\]\s*", re.IGNORECASE | re.MULTILINE)
BALANCE_RE = re.compile(r"Balance:\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
STATUS_RE = re.compile(r"Registration Status:\s*([A-Za-z]+)", re.IGNORECASE)
CHANNEL_RE = re.compile(r"Channel address:\s*(0x[0-9a-fA-F]{40})", re.IGNORECASE)
KNOWN_ORDER_STATES = {"initial", "new", "paid", "failed"}
INCOMPLETE_ORDER_STATES = {"initial", "new"}
PAYMENT_URL_KEYS = {
    "payment_url",
    "pay_url",
    "payment_link",
    "redirect_url",
    "checkout_url",
    "url",
}


class WorkflowError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def combined(self) -> str:
        return self.stdout + self.stderr


@dataclass(frozen=True)
class IdentityDetails:
    address: str
    registration: str
    balance: Decimal
    channel_address: str | None


@dataclass(frozen=True)
class OrderSummary:
    order_id: str
    state: str


@dataclass(frozen=True)
class Gateway:
    name: str
    minimum: Decimal
    currencies: tuple[str, ...]


class MystContainer:
    def __init__(self, engine: str, name: str):
        self.engine = engine
        self.name = name
        self.flags = (
            "--config-dir=/var/lib/mysterium-node",
            "--script-dir=/etc/mysterium-node",
            "--data-dir=/var/lib/mysterium-node",
            "--runtime-dir=/var/run/mysterium-node",
        )

    def is_running(self) -> bool:
        result = subprocess.run(
            [self.engine, "inspect", "-f", "{{.State.Running}}", self.name],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"

    def run(self, *args: str) -> CommandResult:
        result = subprocess.run(
            [self.engine, "exec", self.name, "myst", *self.flags, *args],
            capture_output=True,
            text=True,
            check=False,
        )
        return CommandResult(result.returncode, result.stdout, result.stderr)

    def cli(self, *args: str) -> CommandResult:
        return self.run("cli", "--agreed-terms-and-conditions", *args)


def clean(value: str) -> str:
    return ANSI_RE.sub("", value)


def safe_detail(value: str) -> str:
    """Keep upstream diagnostics visible without allowing terminal controls."""
    return "".join(
        character
        for character in clean(value)
        if character in "\n\t"
        or (ord(character) >= 0x20 and ord(character) != 0x7F)
    )


def require_success(result: CommandResult, description: str) -> str:
    combined = clean(result.combined)
    error_marker = CLI_ERROR_RE.search(combined) is not None
    if result.returncode != 0 or error_marker:
        detail = safe_detail(result.combined).strip()
        suffix = f": {detail}" if detail else ""
        raise WorkflowError(
            f"{description} failed (exit {result.returncode}, "
            f"error_marker={error_marker}){suffix}"
        )
    return clean(result.stdout)


def wait_for_tequilapi(
    myst: MystContainer,
    *,
    attempts: int = 40,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    print("Waiting for Myst daemon (TequilAPI) to be ready...")
    for attempt in range(attempts):
        if myst.run("connection", "info").returncode == 0:
            print("TequilAPI is ready.")
            return
        if attempt + 1 < attempts:
            sleeper(3)
    raise WorkflowError("TequilAPI did not become reachable within 120 seconds")


def list_identities(myst: MystContainer) -> list[str]:
    output = require_success(myst.cli("identities", "list"), "identity listing")
    identities = []
    for match in IDENTITY_LINE_RE.findall(output):
        normalized = match.lower()
        if normalized not in identities:
            identities.append(normalized)
    return identities


def select_identity(myst: MystContainer, configured: str | None) -> str:
    identities = list_identities(myst)
    if not identities:
        print("No Myst identity found. Creating exactly one identity...")
        require_success(myst.cli("identities", "new"), "identity creation")
        identities = list_identities(myst)
        if len(identities) != 1:
            raise WorkflowError(
                f"identity creation postcondition failed: expected one identity, found {len(identities)}"
            )
    if configured:
        normalized = configured.lower()
        if not IDENTITY_RE.fullmatch(configured) or normalized not in identities:
            raise WorkflowError("MYST_VPN_IDENTITY does not select an available identity")
        return normalized
    if len(identities) != 1:
        raise WorkflowError(
            "multiple Myst identities exist; set MYST_VPN_IDENTITY explicitly before continuing"
        )
    return identities[0]


def unlock_identity(myst: MystContainer, identity: str) -> None:
    require_success(
        myst.cli("identities", "unlock", identity),
        f"unlocking identity {identity}",
    )


def identity_details(myst: MystContainer, identity: str) -> IdentityDetails:
    output = require_success(
        myst.cli("identities", "get", identity), f"reading identity {identity}"
    )
    status_match = STATUS_RE.search(output)
    balance_match = BALANCE_RE.search(output)
    if status_match is None or balance_match is None:
        raise WorkflowError("Myst identity output is missing registration status or balance")
    try:
        balance = Decimal(balance_match.group(1))
    except InvalidOperation as exc:
        raise WorkflowError("Myst identity balance is malformed") from exc
    channel_match = CHANNEL_RE.search(output)
    return IdentityDetails(
        address=identity,
        registration=status_match.group(1).lower(),
        balance=balance,
        channel_address=channel_match.group(1).lower() if channel_match else None,
    )


def refreshed_identity_details(myst: MystContainer, identity: str) -> IdentityDetails:
    output = require_success(
        myst.cli("identities", "balance", identity),
        f"refreshing balance for {identity}",
    )
    if BALANCE_RE.search(output) is None:
        raise WorkflowError("Myst balance refresh output is malformed")
    return identity_details(myst, identity)


def ensure_registration(
    myst: MystContainer,
    identity: str,
    *,
    wait_seconds: int = 120,
    sleeper: Callable[[float], None] = time.sleep,
) -> IdentityDetails:
    details = identity_details(myst, identity)
    if details.registration in {"registered", "inprogress"}:
        return details
    if details.registration not in {"unregistered", "registrationerror"}:
        raise WorkflowError(
            f"refusing registration from unknown state {details.registration!r}"
        )
    print(f"Submitting one registration request for exact identity {identity}...")
    require_success(
        myst.cli("identities", "register", identity),
        f"registration request for {identity}",
    )
    deadline = time.monotonic() + wait_seconds
    while True:
        details = identity_details(myst, identity)
        if details.registration in {"registered", "inprogress"}:
            print(f"Registration state is now {details.registration}.")
            return details
        if details.registration not in {"unregistered", "registrationerror"}:
            raise WorkflowError(
                f"registration entered unknown state {details.registration!r}"
            )
        if time.monotonic() >= deadline:
            raise WorkflowError(
                f"registration did not reach registered/inprogress within {wait_seconds}s; "
                "the container remains available for a later explicit retry"
            )
        sleeper(5)


def parse_orders(output: str) -> list[OrderSummary]:
    cleaned = clean(output)
    orders = [OrderSummary(order_id, state.lower()) for order_id, state in ORDER_RE.findall(cleaned)]
    if not orders:
        if re.search(r"No orders found", cleaned, re.IGNORECASE):
            return []
        raise WorkflowError("Myst order listing output is unrecognized")
    if any(not ORDER_ID_RE.fullmatch(order.order_id) for order in orders):
        raise WorkflowError("Myst order listing contains an unsafe or malformed order ID")
    unknown = sorted({order.state for order in orders} - KNOWN_ORDER_STATES)
    if unknown:
        raise WorkflowError(f"Myst returned unknown order state(s): {', '.join(unknown)}")
    if len({order.order_id for order in orders}) != len(orders):
        raise WorkflowError("Myst order listing contains duplicate order IDs")
    return orders


def list_orders(myst: MystContainer, identity: str) -> list[OrderSummary]:
    output = require_success(
        myst.cli("orders", "get-all", identity), f"listing orders for {identity}"
    )
    return parse_orders(output)


def order_detail(myst: MystContainer, identity: str, order_id: str) -> tuple[OrderSummary, str]:
    output = require_success(
        myst.cli("orders", "get", identity, order_id), f"reading order {order_id}"
    )
    parsed = parse_orders(output)
    if len(parsed) != 1 or parsed[0].order_id != order_id:
        raise WorkflowError(f"order {order_id} detail postcondition failed")
    return parsed[0], output


def _known_url_values(value: object) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in PAYMENT_URL_KEYS and isinstance(child, str):
                yield child
            elif isinstance(child, (dict, list)):
                yield from _known_url_values(child)
    elif isinstance(value, list):
        for child in value:
            if isinstance(child, (dict, list)):
                yield from _known_url_values(child)


def extract_payment_url(output: str) -> str:
    data_lines = []
    for line in clean(output).splitlines():
        if "Data:" in line:
            data_lines.append(line.rsplit("Data:", 1)[1].strip())
    if len(data_lines) != 1:
        raise WorkflowError("order detail must contain exactly one Data JSON value")
    try:
        payload = json.loads(data_lines[0])
    except json.JSONDecodeError as exc:
        raise WorkflowError("order Data value is not valid JSON") from exc
    urls = list(dict.fromkeys(_known_url_values(payload)))
    if len(urls) != 1:
        raise WorkflowError("order Data must contain exactly one recognized payment URL")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in urls[0]):
        raise WorkflowError("payment URL contains control characters")
    try:
        parsed = urlsplit(urls[0])
        hostname = parsed.hostname
    except ValueError as exc:
        raise WorkflowError("payment URL is malformed") from exc
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise WorkflowError("payment URL must be credential-free HTTPS with a hostname")
    return urls[0]


def parse_gateways(output: str) -> dict[str, Gateway]:
    gateways: dict[str, Gateway] = {}
    current_name: str | None = None
    current_minimum: Decimal | None = None
    for line in clean(output).splitlines():
        gateway_match = re.search(r"Gateway:\s*([^\s]+)", line, re.IGNORECASE)
        if gateway_match:
            current_name = gateway_match.group(1)
            current_minimum = None
            continue
        minimum_match = re.search(r"Suggested minimum order:\s*([^\s]+)", line, re.IGNORECASE)
        if minimum_match and current_name:
            try:
                current_minimum = Decimal(minimum_match.group(1))
            except InvalidOperation as exc:
                raise WorkflowError("gateway minimum is malformed") from exc
            continue
        currencies_match = re.search(r"Supported currencies:\s*(.+)$", line, re.IGNORECASE)
        if currencies_match and current_name and current_minimum is not None:
            currencies = tuple(
                item.strip().upper()
                for item in currencies_match.group(1).split(",")
                if item.strip()
            )
            if not currencies:
                raise WorkflowError(f"gateway {current_name} has no currencies")
            if (
                not GATEWAY_NAME_RE.fullmatch(current_name)
                or not current_minimum.is_finite()
                or current_minimum < 0
                or any(not re.fullmatch(r"[A-Z0-9._-]{1,20}", item) for item in currencies)
            ):
                raise WorkflowError(f"gateway {current_name!r} has malformed metadata")
            if current_name in gateways:
                raise WorkflowError(f"gateway listing contains duplicate {current_name!r}")
            gateways[current_name] = Gateway(current_name, current_minimum, currencies)
            current_name = None
            current_minimum = None
    if not gateways:
        raise WorkflowError("Myst gateway listing output is unrecognized")
    return gateways


def validate_order_configuration(myst: MystContainer) -> tuple[str, str, str, str, str]:
    amount_text = os.environ.get("MYST_VPN_ORDER_AMOUNT", "")
    currency = os.environ.get("MYST_VPN_ORDER_CURRENCY", "").upper()
    gateway_name = os.environ.get("MYST_VPN_ORDER_GATEWAY", "")
    country = os.environ.get("MYST_VPN_ORDER_COUNTRY", "").upper()
    gateway_data = os.environ.get("MYST_VPN_ORDER_GATEWAY_DATA", "")
    missing = [
        name
        for name, value in (
            ("MYST_VPN_ORDER_AMOUNT", amount_text),
            ("MYST_VPN_ORDER_CURRENCY", currency),
            ("MYST_VPN_ORDER_GATEWAY", gateway_name),
            ("MYST_VPN_ORDER_COUNTRY", country),
            ("MYST_VPN_ORDER_GATEWAY_DATA", gateway_data),
        )
        if not value
    ]
    if missing:
        raise WorkflowError("missing required order configuration: " + ", ".join(missing))
    try:
        amount = Decimal(amount_text)
    except InvalidOperation as exc:
        raise WorkflowError("MYST_VPN_ORDER_AMOUNT must be a finite decimal") from exc
    if not amount.is_finite() or amount <= 0:
        raise WorkflowError("MYST_VPN_ORDER_AMOUNT must be a positive finite decimal")
    if not re.fullmatch(r"[A-Z]{2}", country):
        raise WorkflowError("MYST_VPN_ORDER_COUNTRY must be a two-letter country code")
    if not re.fullmatch(r"[A-Z0-9._-]{1,20}", currency):
        raise WorkflowError("MYST_VPN_ORDER_CURRENCY is malformed")
    if not GATEWAY_NAME_RE.fullmatch(gateway_name):
        raise WorkflowError("MYST_VPN_ORDER_GATEWAY is malformed")
    for item in gateway_data.split(","):
        if item.count("=") != 1:
            raise WorkflowError("MYST_VPN_ORDER_GATEWAY_DATA must contain key=value pairs")
        key, value = item.split("=", 1)
        if (
            not GATEWAY_DATA_KEY_RE.fullmatch(key)
            or not value
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
        ):
            raise WorkflowError("MYST_VPN_ORDER_GATEWAY_DATA contains an invalid key or value")
    gateway_output = require_success(myst.cli("orders", "gateways"), "gateway listing")
    gateways = parse_gateways(gateway_output)
    gateway = gateways.get(gateway_name)
    if gateway is None:
        raise WorkflowError(f"configured gateway {gateway_name!r} is not currently available")
    if currency not in gateway.currencies:
        raise WorkflowError(
            f"currency {currency!r} is not supported by gateway {gateway_name!r}"
        )
    if amount <= gateway.minimum:
        raise WorkflowError(
            f"order amount must be greater than the current gateway minimum {gateway.minimum} MYST"
        )
    return amount_text, currency, gateway_name, country, gateway_data


def print_payment(order: OrderSummary, output: str) -> None:
    if order.state not in INCOMPLETE_ORDER_STATES:
        raise WorkflowError(f"refusing to display a payment URL for {order.state} order")
    url = extract_payment_url(output)
    host = urlsplit(url).hostname
    print("═══════════════════════════════════════════════════════════")
    print(f"Order: {order.order_id} ({order.state})")
    print(f"Verified HTTPS payment host: {host}")
    print(f"PAYMENT URL: {url}")
    print("═══════════════════════════════════════════════════════════")


def create_order(myst: MystContainer, identity: str, before: list[OrderSummary]) -> None:
    amount, currency, gateway, country, gateway_data = validate_order_configuration(myst)
    print(
        f"Creating one order: amount={amount} MYST, pay_currency={currency}, "
        f"gateway={gateway}, country={country}"
    )
    created = myst.cli(
        "orders", "create", identity, amount, currency, gateway, country, gateway_data
    )
    create_output = clean(created.combined)
    create_ids = [order_id for order_id, _state in ORDER_RE.findall(create_output)]
    if any(not ORDER_ID_RE.fullmatch(order_id) for order_id in create_ids):
        raise WorkflowError("order creation returned an unsafe or malformed order ID")
    after = list_orders(myst, identity)
    before_ids = {order.order_id for order in before}
    new_orders = [order for order in after if order.order_id not in before_ids]
    reported_ids = list(dict.fromkeys(create_ids))
    if len(new_orders) != 1 or (
        reported_ids and reported_ids != [new_orders[0].order_id]
    ):
        detail = safe_detail(created.combined).strip()
        raise WorkflowError(
            "order creation postcondition is ambiguous; no retry was attempted"
            + (f": {detail}" if detail else "")
        )
    order_id = new_orders[0].order_id
    order, detail_output = order_detail(myst, identity, order_id)
    if created.returncode != 0:
        print("Order command failed, but exact-order verification found the created order.")
    print_payment(order, detail_output)


def prepare_identity(myst: MystContainer) -> tuple[str, IdentityDetails]:
    configured = os.environ.get("MYST_VPN_IDENTITY") or None
    identity = select_identity(myst, configured)
    unlock_identity(myst, identity)
    return identity, refreshed_identity_details(myst, identity)


def cmd_signup(myst: MystContainer) -> None:
    wait_for_tequilapi(myst)
    identity, details = prepare_identity(myst)
    print(f"Identity: {identity}")
    print(f"Registration: {details.registration}")
    print(f"Balance: {details.balance} MYST")
    if details.balance > 0:
        print("Wallet is funded; no order was created.")
        return
    ensure_registration(myst, identity)
    details = refreshed_identity_details(myst, identity)
    orders = list_orders(myst, identity)
    incomplete = [order for order in orders if order.state in INCOMPLETE_ORDER_STATES]
    if incomplete:
        order, output = order_detail(myst, identity, incomplete[0].order_id)
        print_payment(order, output)
        print("An incomplete order already exists; no new order was created.")
        return
    if any(order.state == "paid" for order in orders) and details.balance == 0:
        raise WorkflowError(
            "a paid order exists but refreshed balance is still zero; wait for settlement "
            "instead of creating another order"
        )
    create_order(myst, identity, orders)


def cmd_orderstatus(myst: MystContainer) -> None:
    wait_for_tequilapi(myst)
    identity, details = prepare_identity(myst)
    orders = list_orders(myst, identity)
    print(f"Identity: {identity}")
    print(f"Registration: {details.registration}")
    print(f"Balance: {details.balance} MYST")
    if not orders:
        print("No payment orders exist.")
    for order in orders:
        print(f"Order {order.order_id}: {order.state}")
        if order.state in INCOMPLETE_ORDER_STATES:
            detail, output = order_detail(myst, identity, order.order_id)
            print_payment(detail, output)
    if details.balance > 0:
        print("Balance is funded; the stack can be started.")
    elif any(order.state == "paid" for order in orders):
        print("Payment is marked paid but balance settlement is still pending.")
    else:
        print("Balance is still zero.")


def cmd_balance(myst: MystContainer) -> None:
    wait_for_tequilapi(myst)
    identity, details = prepare_identity(myst)
    print(f"Identity: {identity}")
    print(f"Registration: {details.registration}")
    print(f"Balance: {details.balance} MYST")
    print(f"Channel Address: {details.channel_address or 'unavailable'}")


def cmd_blockchain(myst: MystContainer) -> None:
    wait_for_tequilapi(myst)
    identity, details = prepare_identity(myst)
    if details.balance > 0:
        print(f"Wallet is already funded with {details.balance} MYST.")
        return
    ensure_registration(myst, identity)
    details = identity_details(myst, identity)
    if details.channel_address is None or details.channel_address == identity:
        raise WorkflowError("Myst did not provide a distinct channel address")
    print("═══════════════════════════════════════════════════════════")
    print("DIRECT TRANSFER INSTRUCTIONS")
    print("Chain: Polygon Mainnet (Chain ID 137)")
    print("MYST token: 0x1379e8886a944d2d9d440b3d88df536aea08d9f3")
    print(f"Send $MYST to: {details.channel_address}")
    print(f"Do NOT send to identity address: {identity}")
    print("═══════════════════════════════════════════════════════════")


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in {"signup", "orderstatus", "balance", "blockchain"}:
        print("Usage: myst-vpn-cli.sh {signup|orderstatus|balance|blockchain}", file=sys.stderr)
        return 2
    myst = MystContainer(
        os.environ.get("CONTAINER_BIN", "docker"),
        os.environ.get("CONTAINER_NAME", "myst-client-vpn"),
    )
    if not myst.is_running():
        print(f"ERROR: Myst container {myst.name!r} is not running.", file=sys.stderr)
        return 1
    commands = {
        "signup": cmd_signup,
        "orderstatus": cmd_orderstatus,
        "balance": cmd_balance,
        "blockchain": cmd_blockchain,
    }
    try:
        commands[args[0]](myst)
    except WorkflowError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
