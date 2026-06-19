#!/bin/sh
# shellcheck shell=dash
# SearXNG proxy entrypoint wrapper.
#
# Mounted as the searxng-core entrypoint by docker-compose.proxy.yml when
# PROXY_URL is non-empty. Merges an `outgoing.proxies` block into the mounted
# /etc/searxng/settings.yml, then execs the SearXNG image's original
# docker-entrypoint.sh (which preserves an existing settings.yml and starts
# uwsgi).
#
# SearXNG's httpx network client builds explicit transport mounts from
# outgoing.proxies and does NOT honor HTTP_PROXY/HTTPS_PROXY env vars when an
# explicit transport is configured, so the proxy must be merged into
# settings.yml rather than passed as an env var.
#
# When PROXY_URL is empty this wrapper is not mounted (the Makefile only
# applies docker-compose.proxy.yml when PROXY_URL is non-empty), so the
# no-proxy path is unchanged.
set -eu

SETTINGS_YML="/etc/searxng/settings.yml"
PROXY_URL="${PROXY_URL:-}"

if [ -z "$PROXY_URL" ]; then
  echo "searxng-proxy-entrypoint: PROXY_URL is empty; nothing to do." >&2
  exec /usr/local/searxng/dockerfiles/docker-entrypoint.sh "$@"
fi

if [ ! -f "$SETTINGS_YML" ]; then
  echo "searxng-proxy-entrypoint: $SETTINGS_YML not found; cannot merge proxy." >&2
  echo "                       Proceeding without proxy (direct egress)." >&2
  exec /usr/local/searxng/dockerfiles/docker-entrypoint.sh "$@"
fi

# Merge the outgoing.proxies block using Python (the SearXNG image ships it).
# We avoid a YAML library dependency by doing a targeted text insertion:
#   - If an `outgoing:` block exists, insert `proxies:` + `extra_proxy_timeout:`
#     under it (before the next top-level key), only if `proxies:` is not
#     already present.
#   - If no `outgoing:` block exists, append one at the end.
# This is robust against the wrapper being re-run (idempotent).
python3 - "$SETTINGS_YML" "$PROXY_URL" <<'PYEOF'
import sys
import re

path = sys.argv[1]
proxy_url = sys.argv[2]

with open(path, "r", encoding="utf-8") as fh:
    lines = fh.readlines()

# Locate the `outgoing:` top-level block (column-0 key).
outgoing_idx = None
for i, line in enumerate(lines):
    if re.match(r"^outgoing:\s*$", line):
        outgoing_idx = i
        break

# Check if a `proxies:` key already exists anywhere under `outgoing:`.
def has_proxies(lines, start):
    for line in lines[start + 1:]:
        # Stop at the next top-level key (column 0, non-space, not comment).
        if line and not line[0].isspace() and not line.lstrip().startswith("#"):
            break
        if re.match(r"^\s+proxies:\s*$", line):
            return True
    return False

block = (
    "  proxies:\n"
    "    all://:\n"
    f"      - {proxy_url}\n"
    "  extra_proxy_timeout: 10.0\n"
)

if outgoing_idx is not None:
    if has_proxies(lines, outgoing_idx):
        print("searxng-proxy-entrypoint: outgoing.proxies already present; "
              "not modifying.", file=sys.stderr)
        sys.exit(0)
    # Find the insertion point: after the last indented line under `outgoing:`.
    insert_at = outgoing_idx + 1
    while insert_at < len(lines):
        line = lines[insert_at]
        if line and not line[0].isspace() and not line.lstrip().startswith("#"):
            break
        insert_at += 1
    # Insert a blank line separator if the previous line is not blank.
    new_lines = lines[:outgoing_idx + 1] + [block] + lines[outgoing_idx + 1:]
    print(f"searxng-proxy-entrypoint: merged outgoing.proxies -> {proxy_url}",
          file=sys.stderr)
else:
    # No outgoing: block; append one.
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    new_lines = lines + ["\n", "outgoing:\n", block]
    print(f"searxng-proxy-entrypoint: appended outgoing.proxies -> {proxy_url}",
          file=sys.stderr)

with open(path, "w", encoding="utf-8") as fh:
    fh.writelines(new_lines)
PYEOF

# Hand off to the SearXNG image's original entrypoint. It will see the existing
# settings.yml and preserve it (without -f it copies defaults to settings.yml.new),
# then start uwsgi.
exec /usr/local/searxng/dockerfiles/docker-entrypoint.sh "$@"
