#!/bin/sh
# shellcheck shell=dash
# SearXNG proxy entrypoint wrapper.
#
# Mounted as the searxng-core entrypoint by docker-compose.proxy.yml when
# PROXY_URL is non-empty. Merges an `outgoing.proxies` block into a
# container-local COPY of the mounted /etc/searxng/settings.yml, points
# SEARXNG_SETTINGS_PATH at the merged copy, then execs the SearXNG image's
# original entrypoint (/usr/local/searxng/entrypoint.sh).
#
# The mounted settings.yml is NEVER modified in place — the merge happens on a
# private copy under /tmp/searxng-proxy/ so the host-side bind mount stays
# pristine and the merge is reproducible across restarts.
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

SEARXNG_ENTRYPOINT="/usr/local/searxng/entrypoint.sh"
SRC_SETTINGS="/etc/searxng/settings.yml"
PROXY_URL="${PROXY_URL:-}"

if [ -z "$PROXY_URL" ]; then
  echo "searxng-proxy-entrypoint: PROXY_URL is empty; nothing to do." >&2
  exec "$SEARXNG_ENTRYPOINT" "$@"
fi

if [ ! -f "$SRC_SETTINGS" ]; then
  echo "searxng-proxy-entrypoint: $SRC_SETTINGS not found; cannot merge proxy." >&2
  echo "                       Proceeding without proxy (direct egress)." >&2
  exec "$SEARXNG_ENTRYPOINT" "$@"
fi

# Work on a private copy so the host-side bind mount is never modified.
# Copy ALL config files from /etc/searxng/ (settings.yml, etc.) because SearXNG
# looks for companion config files in the same directory as
# SEARXNG_SETTINGS_PATH. Also copy limiter.toml from the image's built-in
# location (it's not in the bind-mounted /etc/searxng/ — only settings.yml is).
MERGE_DIR="/tmp/searxng-proxy"
mkdir -p "$MERGE_DIR"
cp -r /etc/searxng/* "$MERGE_DIR/" 2>/dev/null || true
# limiter.toml ships in the image at /usr/local/searxng/searx/limiter.toml but
# is not in the bind-mounted /etc/searxng/ directory. Copy it to the merge dir
# so SearXNG's bot detection finds it alongside settings.yml.
if [ ! -f "$MERGE_DIR/limiter.toml" ] && [ -f /usr/local/searxng/searx/limiter.toml ]; then
  cp /usr/local/searxng/searx/limiter.toml "$MERGE_DIR/limiter.toml"
fi
MERGED_SETTINGS="$MERGE_DIR/settings.yml"
# Ensure settings.yml is the fresh source copy (cp -r may have copied a stale
# merged version from a previous run if /etc/searxng was the merge dir).
cp "$SRC_SETTINGS" "$MERGED_SETTINGS"

# Merge proxy config into the settings copy using Python (the SearXNG image
# ships it). We avoid a YAML library dependency by doing targeted text edits:
#
#  1. outgoing.proxies: insert a `proxies:` block (all:// -> PROXY_URL) under
#     `outgoing:` so all external engine requests egress through the proxy.
#     extra_proxy_timeout is an int (SearXNG schema: SettingsValue(int, 0)).
#
#  2. outgoing.networks.direct: define a `direct` network with `proxies: {}`
#     (empty = no proxy) so engines assigned to it bypass the proxy entirely.
#
#  3. crw-backed engines (google2, brave2, duckduckgo2): set `network: direct`
#     so their loopback POSTs to http://127.0.0.1:3010/v1/scrape (the local crw
#     Firecrawl-compatible scraper) are NOT sent through the upstream proxy.
#     Without this, the `all://` proxy pattern catches the loopback crw request
#     and the proxy rejects it ("private address" / SOCKS failure).
python3 - "$MERGED_SETTINGS" "$PROXY_URL" <<'PYEOF'
import sys
import re

path = sys.argv[1]
proxy_url = sys.argv[2]
CRW_ENGINES = {"google2", "brave2", "duckduckgo2"}

with open(path, "r", encoding="utf-8") as fh:
    lines = fh.readlines()

def find_top_level_block(name):
    """Return (header_idx, end_idx) of a top-level `name:` block, or (None, None)."""
    header = None
    for i, line in enumerate(lines):
        if re.match(rf"^{re.escape(name)}:\s*$", line):
            header = i
            break
    if header is None:
        return None, None
    end = len(lines)
    for j in range(header + 1, len(lines)):
        line = lines[j]
        if line and not line[0].isspace() and not line.lstrip().startswith("#"):
            end = j
            break
    return header, end

def has_key_under(block_start, key):
    """True if a real (non-commented) `key:` exists as a direct child of the block."""
    for line in lines[block_start + 1:]:
        if line and not line[0].isspace() and not line.lstrip().startswith("#"):
            break
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if re.match(rf"^{re.escape(key)}:\s*$", stripped):
            return True
    return False

# ── 1. outgoing.proxies ──────────────────────────────────────────────────
outgoing_idx, outgoing_end = find_top_level_block("outgoing")
proxy_block = (
    "  proxies:\n"
    "    all://:\n"
    f"      - {proxy_url}\n"
    "  extra_proxy_timeout: 20\n"
)

if outgoing_idx is not None:
    if has_key_under(outgoing_idx, "proxies"):
        print("searxng-proxy-entrypoint: outgoing.proxies already present; "
              "not modifying proxies.", file=sys.stderr)
    else:
        lines = lines[:outgoing_idx + 1] + [proxy_block] + lines[outgoing_idx + 1:]
        print(f"searxng-proxy-entrypoint: merged outgoing.proxies -> {proxy_url}",
              file=sys.stderr)
else:
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines += ["\n", "outgoing:\n", proxy_block]
    print(f"searxng-proxy-entrypoint: appended outgoing.proxies -> {proxy_url}",
          file=sys.stderr)

# ── 2. outgoing.networks.direct ──────────────────────────────────────────
# Re-locate outgoing (indices may have shifted after the proxies insertion).
outgoing_idx, outgoing_end = find_top_level_block("outgoing")
networks_block = (
    "  networks:\n"
    "    direct:\n"
    "      proxies: {}\n"
    "      enable_http: true\n"
)
if outgoing_idx is not None and not has_key_under(outgoing_idx, "networks"):
    lines = lines[:outgoing_end] + [networks_block] + lines[outgoing_end:]
    print("searxng-proxy-entrypoint: added outgoing.networks.direct (no-proxy)",
          file=sys.stderr)
elif outgoing_idx is not None:
    print("searxng-proxy-entrypoint: outgoing.networks already present; "
          "not adding direct network.", file=sys.stderr)

# ── 3. crw-backed engines: network: direct ───────────────────────────────
# Find each `- name: <engine>` entry under the `engines:` list and inject
# `network: direct` if not already present.
engines_idx, _ = find_top_level_block("engines")
if engines_idx is not None:
    i = engines_idx + 1
    while i < len(lines):
        line = lines[i]
        # Stop at the next top-level key.
        if line and not line[0].isspace() and not line.lstrip().startswith("#"):
            break
        m = re.match(r"^  - name:\s*(\S+)\s*$", line)
        if m and m.group(1) in CRW_ENGINES:
            # Check if `network:` already exists in this engine entry.
            has_network = False
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if re.match(r"^  - name:", nxt) or (
                    nxt and not nxt[0].isspace() and not nxt.lstrip().startswith("#")
                ):
                    break
                stripped = nxt.lstrip()
                if not stripped.startswith("#") and re.match(r"^network:\s*\S", stripped):
                    has_network = True
                    break
                j += 1
            if not has_network:
                lines.insert(i + 1, "    network: direct\n")
                print(f"searxng-proxy-entrypoint: set network: direct on "
                      f"engine '{m.group(1)}'", file=sys.stderr)
            else:
                print(f"searxng-proxy-entrypoint: engine '{m.group(1)}' already "
                      f"has a network; not overriding.", file=sys.stderr)
        i += 1

with open(path, "w", encoding="utf-8") as fh:
    fh.writelines(lines)
PYEOF

# Point SearXNG at the merged copy. The image's entrypoint honors
# SEARXNG_SETTINGS_PATH and will preserve an existing file (without -f it
# copies defaults to settings.yml.new).
export SEARXNG_SETTINGS_PATH="$MERGED_SETTINGS"

# Hand off to the SearXNG image's original entrypoint.
exec "$SEARXNG_ENTRYPOINT" "$@"
