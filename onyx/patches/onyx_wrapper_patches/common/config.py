"""common config patch implementation."""

from __future__ import annotations

import os
from urllib.parse import urlsplit


EFFECTIVE_UNLIMITED_CHARS = 2_000_000_000



def _strict_mode() -> bool:
    return os.environ.get("WRAPPER_PATCH_STRICT", "true").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )



def _warn_or_raise(message: str) -> None:
    print(f"sitecustomize: WARNING: {message}", flush=True)
    if _strict_mode():
        raise RuntimeError(message)



def _raise_if_strict() -> None:
    if _strict_mode():
        raise



def _replace_or_warn(
    *,
    owner_name: str,
    current: str,
    old: str,
    new: str,
) -> str:
    replaced = current.replace(old, new)
    if replaced == current:
        _warn_or_raise(
            f"{owner_name} patch did not match expected upstream text"
        )
    else:
        print(f"sitecustomize: patched {owner_name}", flush=True)
    return replaced



def _parse_positive_int(var_name: str) -> int | None:
    raw = os.environ.get(var_name)
    if not raw:
        return None

    try:
        value = int(raw)
    except ValueError:
        print(
            f"sitecustomize: ignoring {var_name}={raw!r} (must be integer)",
            flush=True,
        )
        return None

    if value == 0:
        print(
            f"sitecustomize: {var_name}=0 -> using effectively unlimited budget "
            f"({EFFECTIVE_UNLIMITED_CHARS})",
            flush=True,
        )
        return EFFECTIVE_UNLIMITED_CHARS

    if value < 0:
        print(
            f"sitecustomize: ignoring {var_name}={raw!r} (must be >= 0)",
            flush=True,
        )
        return None

    return value



def _parse_optional_positive_int(var_name: str) -> int | None:
    raw = os.environ.get(var_name)
    if not raw:
        return None

    try:
        value = int(raw)
    except ValueError:
        print(
            f"sitecustomize: ignoring {var_name}={raw!r} (must be integer)",
            flush=True,
        )
        return None

    if value == 0:
        return None

    if value < 0:
        print(
            f"sitecustomize: ignoring {var_name}={raw!r} (must be >= 0)",
            flush=True,
        )
        return None

    return value



def _env_flag_enabled(var_name: str) -> bool:
    return os.environ.get(var_name, "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )



def _env_flag_default_true(var_name: str) -> bool:
    return os.environ.get(var_name, "true").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )



def _required_positive_int(var_name: str, default: int) -> int:
    raw = os.environ.get(var_name, str(default))
    try:
        value = int(raw)
    except ValueError:
        _warn_or_raise(f"{var_name} must be a positive integer, got {raw!r}")
        return default
    if value <= 0:
        _warn_or_raise(f"{var_name} must be a positive integer, got {raw!r}")
        return default
    return value





def validate_fixed_proxy_value(value: str, expected_url: str) -> str:
    """Accept only stripped exact canonical values; never interpret URL aliases."""
    value = value.strip()
    if value != expected_url:
        raise RuntimeError(f"fixed proxy must be exactly {expected_url}")
    return value


def _validated_fixed_proxy_url(env_name: str, expected_host: str) -> str:
    expected_url = f"http://{expected_host}:3128"
    try:
        return validate_fixed_proxy_value(os.environ.get(env_name, ""), expected_url)
    except RuntimeError as exc:
        raise RuntimeError(f"{env_name} must be exactly {expected_url}") from exc
