#!/bin/bash

set -eo pipefail

CONTAINER_BIN="${CONTAINER_BIN:-docker}"
INSTALL_SCRIPT="${ONYX_INSTALL_SCRIPT:-./install.sh}"

if [[ "$CONTAINER_BIN" == */* ]]; then
	if [[ ! -x "$CONTAINER_BIN" ]]; then
		echo "ERROR: CONTAINER_BIN is not executable: $CONTAINER_BIN" >&2
		exit 1
	fi
	RESOLVED_CONTAINER_BIN="$CONTAINER_BIN"
else
	RESOLVED_CONTAINER_BIN="$(command -v "$CONTAINER_BIN" 2>/dev/null || true)"
	if [[ -z "$RESOLVED_CONTAINER_BIN" ]]; then
		echo "ERROR: CONTAINER_BIN was not found on PATH: $CONTAINER_BIN" >&2
		exit 1
	fi
fi

if [[ ! -f "$INSTALL_SCRIPT" ]]; then
	echo "ERROR: ONYX_INSTALL_SCRIPT was not found: $INSTALL_SCRIPT" >&2
	exit 1
fi

SHIM_DIR="$(mktemp -d "${TMPDIR:-/tmp}/onyx-container-bin.XXXXXX")"
cleanup() {
	rm -rf "$SHIM_DIR"
}
trap cleanup EXIT

ln -s "$RESOLVED_CONTAINER_BIN" "$SHIM_DIR/docker"

PATH="$SHIM_DIR:$PATH" exec bash "$INSTALL_SCRIPT" "$@"