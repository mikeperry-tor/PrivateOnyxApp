"""Identify exact Python helper processes that must not install SearXNG patches."""

from __future__ import annotations


_RESOURCE_TRACKER_PREFIX = (
    b"from multiprocessing.resource_tracker import main;main("
)


def is_resource_tracker_cmdline(cmdline: bytes) -> bool:
    parts = cmdline.rstrip(b"\0").split(b"\0")
    if len(parts) != 3 or parts[1] != b"-c":
        return False
    program = parts[0].rsplit(b"/", 1)[-1]
    if not program.startswith(b"python"):
        return False
    code = parts[2]
    if not code.startswith(_RESOURCE_TRACKER_PREFIX) or not code.endswith(b")"):
        return False
    descriptor = code[len(_RESOURCE_TRACKER_PREFIX) : -1]
    return bool(descriptor) and descriptor.isdigit()


def current_process_is_resource_tracker() -> bool:
    try:
        with open("/proc/self/cmdline", "rb") as stream:
            return is_resource_tracker_cmdline(stream.read())
    except OSError:
        return False
