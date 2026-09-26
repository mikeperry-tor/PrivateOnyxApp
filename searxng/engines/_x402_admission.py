"""One worker-local API attempt; native processor suspension is authoritative."""

import threading
import time
import uuid
from contextlib import contextmanager

_condition = threading.Condition()
_token = None
_active = False
_last_start = float("-inf")
RESERVATION_PARAM = "_wrapper_x402_reservation"


def reserve(available):
    global _token
    with _condition:
        if not available() or _active or _token is not None or time.monotonic() - _last_start < 3:
            return None
        _token = uuid.uuid4().hex
        return _token


def wait(available, deadline=None):
    with _condition:
        while available():
            token = reserve(available)
            if token:
                return token
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return None
            cooling = 3 - (time.monotonic() - _last_start)
            delay = cooling if cooling > 0 and not _active and _token is None else None
            if remaining is not None:
                delay = min(delay, remaining) if delay is not None else remaining
            _condition.wait(delay)
    return None


def release(token):
    global _token
    with _condition:
        if token is not None and token == _token:
            _token = None
            _condition.notify_all()


@contextmanager
def ownership(token):
    global _token, _active
    with _condition:
        if token is None or _token != token or _active:
            raise RuntimeError("invalid_api_reservation")
        _token = None
        _active = True
    try:
        yield record_start
    finally:
        with _condition:
            _active = False
            _condition.notify_all()


def record_start():
    global _last_start
    with _condition:
        if not _active:
            raise RuntimeError("api_attempt_not_owned")
        _last_start = time.monotonic()
