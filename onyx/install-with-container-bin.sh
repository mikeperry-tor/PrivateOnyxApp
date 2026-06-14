#!/bin/bash

set -eo pipefail

CONTAINER_BIN="${CONTAINER_BIN:-docker}"
PODMAN_COMPOSE_PROVIDER="${PODMAN_COMPOSE_PROVIDER:-podman}"
ONYX_DESIRED_IMAGE_TAG="${ONYX_DESIRED_IMAGE_TAG:-}"
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

RUNTIME_INSTALL_SCRIPT="$INSTALL_SCRIPT"
if [[ -n "$ONYX_DESIRED_IMAGE_TAG" ]]; then
	RUNTIME_INSTALL_SCRIPT="$SHIM_DIR/install.sh"
	sed "s/^DEFAULT_IMAGE_TAG=\"edge\"$/DEFAULT_IMAGE_TAG=\"$ONYX_DESIRED_IMAGE_TAG\"/" "$INSTALL_SCRIPT" > "$RUNTIME_INSTALL_SCRIPT"
	chmod +x "$RUNTIME_INSTALL_SCRIPT"
fi

cat > "$SHIM_DIR/docker" <<'EOF'
#!/bin/bash
set -eo pipefail

if [[ "${1:-}" == "compose" ]]; then
	shift
	exec env -u PODMAN_COMPOSE_PROVIDER "$REAL_CONTAINER_BIN" compose "$@"
fi

if [[ "${1:-}" == "system" && "${2:-}" == "info" ]]; then
	output=""
	if output=$(PODMAN_COMPOSE_PROVIDER="$PODMAN_COMPOSE_PROVIDER" "$REAL_CONTAINER_BIN" "$@" 2>&1); then
		printf '%s\n' "$output"
		if ! printf '%s\n' "$output" | grep -qi 'total memory'; then
			mem_total=$(printf '%s\n' "$output" | sed -n 's/^[[:space:]]*memTotal:[[:space:]]*\([0-9][0-9]*\)$/\1/p' | head -1)
			if [[ -n "$mem_total" ]]; then
				total_memory_gib=$(awk "BEGIN {printf \"%.1f\", $mem_total / 1073741824}")
				printf 'Total Memory: %sGiB\n' "$total_memory_gib"
			fi
		fi
		exit 0
	fi
	status=$?
	printf '%s\n' "$output"
	exit "$status"
fi

exec env PODMAN_COMPOSE_PROVIDER="$PODMAN_COMPOSE_PROVIDER" "$REAL_CONTAINER_BIN" "$@"
EOF
chmod +x "$SHIM_DIR/docker"

REAL_CONTAINER_BIN="$RESOLVED_CONTAINER_BIN" PATH="$SHIM_DIR:$PATH" PODMAN_COMPOSE_PROVIDER="$PODMAN_COMPOSE_PROVIDER" exec bash "$RUNTIME_INSTALL_SCRIPT" "$@"