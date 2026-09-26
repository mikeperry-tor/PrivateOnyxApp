"""Secret-safe, inert validation shared by host preflight and SearXNG."""

import re

KEY_NAME = "SEARXNG_X402_PRIVKEY"
_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


def enabled(key, round_robin="true"):
    if key is None or key == "":
        return False
    if not isinstance(key, str) or not re.fullmatch(r"(?:0x)?[0-9a-fA-F]{64}", key):
        raise ValueError("invalid SEARXNG_X402_PRIVKEY")
    if not 0 < int(key, 16) < _ORDER:
        raise ValueError("invalid SEARXNG_X402_PRIVKEY")
    if round_robin.lower() not in ("true", "1", "yes", "on"):
        raise ValueError("x402exa requires SEARXNG_ROUND_ROBIN=true")
    return True
