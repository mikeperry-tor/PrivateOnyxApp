"""Strict, bounded Anubis proof-of-work protocol support for Startpage search."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable, Literal


ANUBIS_MAIN_PATH = "/.within.website/x/cmd/anubis/static/js/main.mjs"
ANUBIS_PASS_PATH = "/.within.website/x/cmd/anubis/api/pass-challenge"
MAX_ANUBIS_CANDIDATES = 1 << 24
ANUBIS_DEADLINE_CHECK_INTERVAL = 4096


class AnubisProtocolError(ValueError):
    """The challenged document does not match an admitted Anubis profile."""


class AnubisSolverError(RuntimeError):
    """The admitted proof could not be solved inside its local bounds."""


@dataclass(frozen=True)
class AnubisChallenge:
    version: str
    challenge_id: str
    random_data: str
    algorithm: Literal["fast"]
    difficulty: int


@dataclass(frozen=True)
class AnubisSolution:
    response: str
    nonce: int
    elapsed_ms: int


@dataclass(frozen=True)
class PendingAnubisPow:
    continuation_token: str
    challenge: AnubisChallenge


class _AnubisScripts(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._script_id: str | None = None
        self._parts: list[str] = []
        self.scripts: dict[str, str] = {}

    def handle_starttag(self, tag, attrs) -> None:
        if tag.lower() != "script" or self._script_id is not None:
            return
        values = {name.lower(): (value or "") for name, value in attrs}
        element_id = values.get("id")
        if element_id in {"anubis_version", "anubis_challenge"}:
            if element_id in self.scripts:
                raise AnubisProtocolError("duplicate Anubis protocol element")
            self._script_id = element_id
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._script_id is not None:
            self._parts.append(data)
            if sum(map(len, self._parts)) > 16_384:
                raise AnubisProtocolError("Anubis protocol element is oversized")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._script_id is not None:
            self.scripts[self._script_id] = "".join(self._parts)
            self._script_id = None
            self._parts = []


def parse_anubis_challenge(html: str) -> AnubisChallenge:
    """Parse one bounded Anubis fast-proof challenge document."""
    if not isinstance(html, str):
        raise AnubisProtocolError("Anubis document must be text")
    parser = _AnubisScripts()
    try:
        parser.feed(html[:262_144])
        parser.close()
        version = json.loads(parser.scripts["anubis_version"])
        payload = json.loads(parser.scripts["anubis_challenge"])
    except (KeyError, json.JSONDecodeError, TypeError) as exc:
        raise AnubisProtocolError("Anubis protocol JSON is invalid") from exc

    if (
        type(version) is not str
        or not 1 <= len(version) <= 128
        or not version.isprintable()
    ):
        raise AnubisProtocolError("Anubis version is invalid")
    if not isinstance(payload, dict):
        raise AnubisProtocolError("Anubis challenge envelope is invalid")
    challenge = payload.get("challenge")
    rules = payload.get("rules")
    if not isinstance(challenge, dict) or not isinstance(rules, dict):
        raise AnubisProtocolError("Anubis challenge profile is invalid")

    challenge_id = challenge.get("id")
    random_data = challenge.get("randomData")
    algorithm = rules.get("algorithm")
    difficulty = rules.get("difficulty")
    if (
        not isinstance(challenge_id, str)
        or not 1 <= len(challenge_id) <= 256
        or not challenge_id.isprintable()
    ):
        raise AnubisProtocolError("Anubis challenge ID is invalid")
    if (
        not isinstance(random_data, str)
        or not 1 <= len(random_data) <= 4096
        or not random_data.isascii()
        or not random_data.isprintable()
    ):
        raise AnubisProtocolError("Anubis random data is invalid")
    if algorithm != "fast" or challenge.get("method") not in (None, "fast"):
        raise AnubisProtocolError("Anubis algorithm is not admitted")
    if type(difficulty) is not int or not 0 <= difficulty <= 64:
        raise AnubisProtocolError("Anubis difficulty is not admitted")
    challenge_difficulty = challenge.get("difficulty")
    spent = challenge.get("spent")
    if (
        challenge_difficulty is not None
        and (
            type(challenge_difficulty) is not int
            or challenge_difficulty != difficulty
        )
    ) or (spent is not None and spent is not False):
        raise AnubisProtocolError("Anubis challenge state is invalid")
    return AnubisChallenge(version, challenge_id, random_data, "fast", difficulty)


def solve_anubis_fast(
    challenge: AnubisChallenge,
    *,
    deadline: float,
    max_candidates: int = MAX_ANUBIS_CANDIDATES,
    clock: Callable[[], float] = time.monotonic,
) -> AnubisSolution:
    """Return the first valid nonce using constant memory and bounded work."""
    if not math.isfinite(deadline) or max_candidates <= 0:
        raise ValueError("Anubis solver bounds are invalid")
    started = clock()
    prefix = "0" * challenge.difficulty
    data = challenge.random_data
    for nonce in range(max_candidates):
        if nonce % ANUBIS_DEADLINE_CHECK_INTERVAL == 0 and clock() >= deadline:
            raise AnubisSolverError("Anubis proof deadline expired")
        response = hashlib.sha256((data + str(nonce)).encode("ascii")).hexdigest()
        if response.startswith(prefix):
            elapsed_ms = max(0, int((clock() - started) * 1000))
            return AnubisSolution(response, nonce, elapsed_ms)
    raise AnubisSolverError("Anubis proof candidate limit exhausted")


def worker_preload_source(control_name: str) -> str:
    """Build the exact Startpage Anubis-worker suppressor preload."""
    if re.fullmatch(r"__privateOnyxAnubis_[0-9a-f]{32}", control_name) is None:
        raise ValueError("invalid Anubis preload control name")
    return f"""
(() => {{
  const NativeWorker = globalThis.Worker;
  const instances = new Set();
  let active = true;
  const mainPath = {ANUBIS_MAIN_PATH!r};
  const workerPattern = /^\\/\\.within\\.website\\/x\\/cmd\\/anubis\\/static\\/js\\/worker\\/sha256-(?:webcrypto|purejs)\\.mjs$/;
  const hasMainMarker = () => Array.from(document.scripts || []).some((script) => {{
    try {{
      const url = new URL(script.src, location.href);
      return url.origin === location.origin && decodeURIComponent(url.pathname) === mainPath;
    }} catch (_) {{ return false; }}
  }});
  const admitted = (value) => {{
    try {{
      const raw = String(value);
      const url = new URL(raw, location.href);
      if (url.protocol === 'blob:') return url.origin === location.origin && hasMainMarker();
      return url.origin === location.origin && workerPattern.test(decodeURIComponent(url.pathname));
    }} catch (_) {{ return false; }}
  }};
  function InertWorker() {{
    this.onmessage = null;
    this.onerror = null;
    this.onmessageerror = null;
    this._listeners = new Map();
    this._terminated = false;
    instances.add(this);
  }}
  InertWorker.prototype.postMessage = function() {{}};
  InertWorker.prototype.addEventListener = function(type, callback) {{
    if (!this._listeners.has(type)) this._listeners.set(type, new Set());
    this._listeners.get(type).add(callback);
  }};
  InertWorker.prototype.removeEventListener = function(type, callback) {{
    this._listeners.get(type)?.delete(callback);
  }};
  InertWorker.prototype.dispatchEvent = function() {{ return true; }};
  InertWorker.prototype.terminate = function() {{ this._terminated = true; instances.delete(this); }};
  function WrappedWorker(url, options) {{
    if (!new.target) throw new TypeError("Worker constructor requires 'new'");
    if (active && admitted(url)) return new InertWorker();
    return Reflect.construct(NativeWorker, [url, options], new.target);
  }}
  Object.setPrototypeOf(WrappedWorker, NativeWorker);
  WrappedWorker.prototype = NativeWorker.prototype;
  globalThis.Worker = WrappedWorker;
  Object.defineProperty(globalThis, {control_name!r}, {{
    configurable: true,
    value: (operation) => {{
      if (operation === 'status') return {{ active, installed: globalThis.Worker === WrappedWorker, suppressed: instances.size }};
      if (operation !== 'remove') throw new Error('unsupported operation');
      active = false;
      for (const worker of Array.from(instances)) worker.terminate();
      if (globalThis.Worker === WrappedWorker) globalThis.Worker = NativeWorker;
      delete globalThis[{control_name!r}];
      return {{ active: false, installed: globalThis.Worker === WrappedWorker, suppressed: 0 }};
    }}
  }});
}})();
"""
