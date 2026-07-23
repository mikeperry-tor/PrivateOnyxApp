#!/usr/bin/env python3
"""Validate wrapper Tor settings and atomically render the fixed torrc."""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


class ConfigError(ValueError):
    pass


def parse_bool(name: str, value: str) -> bool:
    if value not in {"true", "false"}:
        raise ConfigError(f"{name} must be exactly 'true' or 'false'")
    return value == "true"


def validate_origin(value: str) -> str:
    if not value or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ConfigError("ONYX_WEB_CANONICAL_ORIGIN must be one nonempty origin")
    if "," in value:
        raise ConfigError("ONYX_WEB_CANONICAL_ORIGIN must not be a list")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ConfigError(f"ONYX_WEB_CANONICAL_ORIGIN is invalid: {exc}") from exc
    if parsed.scheme not in {"http", "https"}:
        raise ConfigError("ONYX_WEB_CANONICAL_ORIGIN must use http or https")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ConfigError("ONYX_WEB_CANONICAL_ORIGIN must have a host and no userinfo")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ConfigError(
            "ONYX_WEB_CANONICAL_ORIGIN must not contain a non-root path, query, or fragment"
        )
    if port is not None and not 0 < port <= 65535:
        raise ConfigError("ONYX_WEB_CANONICAL_ORIGIN has an invalid port")
    host = parsed.hostname
    if "*" in host or host.startswith(".") or host.endswith("."):
        raise ConfigError("ONYX_WEB_CANONICAL_ORIGIN has an invalid host")
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ConfigError("ONYX_WEB_CANONICAL_ORIGIN host is not valid IDNA") from exc
    try:
        ipaddress.ip_address(ascii_host)
    except ValueError:
        if len(ascii_host) > 253 or any(
            len(label) > 63
            or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
            for label in ascii_host.split(".")
        ):
            raise ConfigError("ONYX_WEB_CANONICAL_ORIGIN host is not valid IDNA")
    return value


def normalize_country(value: str) -> str:
    if not value:
        return ""
    if not re.fullmatch(r"[A-Za-z]{2}", value) or value.lower() in {"a1"}:
        raise ConfigError(
            "TOR_EXIT_COUNTRY must be exactly two ASCII letters and not A1"
        )
    # ?? is excluded by the ASCII-letter grammar.
    return value.lower()


def normalize_fingerprints(value: str) -> tuple[str, ...]:
    if not value:
        return ()
    items = value.split(",")
    if not 1 <= len(items) <= 16 or any(not item for item in items):
        raise ConfigError(
            "TOR_EXIT_NODE_FINGERPRINTS must contain 1 to 16 nonempty items"
        )
    normalized: list[str] = []
    for item in items:
        candidate = item[1:] if item.startswith("$") else item
        if not re.fullmatch(r"[0-9A-Fa-f]{40}", candidate):
            raise ConfigError(
                "each Tor exit fingerprint must contain exactly 40 hexadecimal characters"
            )
        normalized.append(candidate.upper())
    if len(set(normalized)) != len(normalized):
        raise ConfigError("TOR_EXIT_NODE_FINGERPRINTS must not contain duplicates")
    return tuple(normalized)


def validate_settings(args: argparse.Namespace) -> tuple[bool, bool, str, tuple[str, ...]]:
    egress = parse_bool("TOR_EGRESS_ENABLED", args.egress)
    onion = parse_bool("TOR_ONION_SERVICE_ENABLED", args.onion)
    validate_origin(args.canonical_origin)
    country = normalize_country(args.country)
    fingerprints = normalize_fingerprints(args.fingerprints)
    if (country or fingerprints) and not egress:
        raise ConfigError("Tor exit selectors require TOR_EGRESS_ENABLED=true")
    if country and fingerprints:
        raise ConfigError("Tor country and fingerprint selectors are mutually exclusive")
    if args.upstream_proxy and egress:
        raise ConfigError(
            "TOR_EGRESS_ENABLED=true conflicts with EGRESS_UPSTREAM_PROXY_URL"
        )
    return egress, onion, country, fingerprints


def render_text(
    *, egress: bool, onion: bool, country: str, fingerprints: tuple[str, ...]
) -> str:
    if not (egress or onion):
        raise ConfigError("at least one Tor role must be enabled to render torrc")
    lines = [
        "DataDirectory /var/lib/tor",
        "GeoIPFile /usr/share/tor/geoip",
        "GeoIPv6File /usr/share/tor/geoip6",
        (
            "SocksPort unix:/run/tor-egress/socks WorldWritable RelaxDirModeCheck"
            if egress
            else "SocksPort 0"
        ),
        "DNSPort 0",
        "HTTPTunnelPort 0",
        "TransPort 0",
        "NATDPort 0",
        "ControlPort 0",
        "ControlSocket /run/tor-control/control.sock",
        "ControlSocketsGroupWritable 0",
        "CookieAuthentication 1",
        "CookieAuthFile /run/tor-control/control_auth_cookie",
        "CookieAuthFileGroupReadable 0",
        "ClientOnly 1",
        "ORPort 0",
        "DirPort 0",
        "ExtORPort 0",
        "ExitPolicy reject *:*",
        "Log notice stdout",
    ]
    if country:
        lines.append(f"ExitNodes {{{country}}}")
    elif fingerprints:
        lines.append("ExitNodes " + ",".join(f"${item}" for item in fingerprints))
    if onion:
        lines.extend(
            [
                "HiddenServiceDir /var/lib/tor/onion-service",
                "HiddenServiceVersion 3",
                # Tor 0.4.9.11 accepts only an IP/port or Unix target here; it
                # does not resolve Compose service names in HiddenServicePort.
                "HiddenServicePort 80 10.253.247.3:8080",
            ]
        )
    return "\n".join(lines) + "\n"


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".torrc.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="ascii", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("validate", "render"))
    parser.add_argument("--egress", required=True)
    parser.add_argument("--onion", required=True)
    parser.add_argument("--country", default="")
    parser.add_argument("--fingerprints", default="")
    parser.add_argument("--upstream-proxy", default="")
    parser.add_argument("--canonical-origin", required=True)
    parser.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        egress, onion, country, fingerprints = validate_settings(args)
        if args.action == "render":
            if not args.output:
                raise ConfigError("--output is required for render")
            content = render_text(
                egress=egress,
                onion=onion,
                country=country,
                fingerprints=fingerprints,
            )
            atomic_write(Path(args.output), content)
    except (ConfigError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
