#!/bin/sh
set -eu

OS_DIR_DATA="/var/lib/mysterium-node"
OS_DIR_RUN="/var/run/mysterium-node"
OS_DIR_CONFIG="/etc/mysterium-node"
MYST="myst --config-dir=${OS_DIR_DATA} --script-dir=${OS_DIR_CONFIG} --data-dir=${OS_DIR_DATA} --runtime-dir=${OS_DIR_RUN}"
MYST_HEALTH_STATE_DIR="/run/myst-healthcheck"

myst_cli() {
  $MYST cli --agreed-terms-and-conditions "$@"
}

is_rfc1918_ipv4_literal() {
  _value="$1"
  case "${_value}" in
    *[!0-9.]* | .* | *. | *..*) return 1 ;;
  esac
  OLDIFS="$IFS"
  IFS='.'
  set -- ${_value}
  IFS="$OLDIFS"
  [ "$#" -eq 4 ] || return 1
  for _octet in "$@"; do
    [ "${_octet}" -le 255 ] 2>/dev/null || return 1
  done
  case "$1" in
    10) return 0 ;;
    192) [ "$2" -eq 168 ] ; return ;;
    172) [ "$2" -ge 16 ] && [ "$2" -le 31 ] ; return ;;
  esac
  return 1
}

# A configured RFC1918 proxy is routing infrastructure, not permission for
# arbitrary RFC1918 targets. Give only an IPv4-literal proxy endpoint an exact
# host route; named operator-local proxies continue to require the explicit
# RFC1918 mode so their complete system-DNS answer set can be classified.
if [ -n "${EGRESS_UPSTREAM_PROXY_URL:-}" ]; then
  case "${EGRESS_UPSTREAM_PROXY_URL}" in
    http://* | https://* | socks5://* | socks5h://*)
      _proxy_authority="${EGRESS_UPSTREAM_PROXY_URL#*://}"
      ;;
    *) _proxy_authority="" ;;
  esac
  case "${_proxy_authority}" in
    */* | *\?* | *\#*) _proxy_authority="" ;;
  esac
  _proxy_authority="${_proxy_authority##*@}"
  case "${_proxy_authority}" in
    \[*\]:*) _proxy_host="" ;;
    *:*)
      _proxy_host="${_proxy_authority%:*}"
      _proxy_port="${_proxy_authority##*:}"
      case "${_proxy_port}" in
        "" | *[!0-9]*) _proxy_host="" ;;
        *)
          [ "${_proxy_port}" -ge 1 ] 2>/dev/null || _proxy_host=""
          [ "${_proxy_port}" -le 65535 ] 2>/dev/null || _proxy_host=""
          ;;
      esac
      ;;
    *) _proxy_host="" ;;
  esac
  if [ -n "${_proxy_host}" ] && is_rfc1918_ipv4_literal "${_proxy_host}"; then
    if [ -n "${MYST_ROUTE_EXEMPT_CIDRS:-}" ]; then
      MYST_ROUTE_EXEMPT_CIDRS="${MYST_ROUTE_EXEMPT_CIDRS},${_proxy_host}/32"
    else
      MYST_ROUTE_EXEMPT_CIDRS="${_proxy_host}/32"
    fi
    echo "Configured RFC1918 upstream proxy: added exact route exemption"
  fi
fi

# Optional LAN access: make approved configured integration endpoints routable
# outside the VPN. Destination policy still limits this capability to the host
# route; these exemptions do not grant public/browser/executor callers access.
if [ "${ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS:-false}" = "true" ]; then
  # Common private network ranges per RFC 1918
  LAN_CIDRS="10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
  if [ -n "${MYST_ROUTE_EXEMPT_CIDRS:-}" ]; then
    MYST_ROUTE_EXEMPT_CIDRS="${MYST_ROUTE_EXEMPT_CIDRS},${LAN_CIDRS}"
  else
    MYST_ROUTE_EXEMPT_CIDRS="${LAN_CIDRS}"
  fi
  echo "Configured integration LAN endpoints enabled: added LAN route exemptions"
fi

# A restart reuses the container writable layer. Clear only the two exact
# process-lifetime health state files before either VPN mode starts.
/bin/sh /usr/local/bin/myst-healthcheck.sh reset "${MYST_HEALTH_STATE_DIR}" /proc

. /usr/local/bin/myst-child-process-control.sh
install_shutdown_handler

# ── Optional VPN bypass ────────────────────────────────────────────────────
# When MYST_VPN_ENABLED=false, retain this container only as a lightweight
# readiness sentinel for the shared namespace. netns-holder, not this process,
# owns that namespace. The container healthcheck still verifies that no stale
# myst0 exists and that the direct IPv4 default route is usable, while the Myst
# daemon, kill-switch, connection, and identity/payment paths remain absent.
if [ "${MYST_VPN_ENABLED:-true}" = "false" ]; then
  echo "MYST_VPN_ENABLED=false: Myst daemon disabled; starting no-VPN readiness sentinel."
  sleep infinity &
  svc_pid="$!"
  set +e
  wait "$svc_pid"
  svc_status="$?"
  set -e
  exit "${svc_status}"
fi

# ── VPN-enabled daemon lifecycle ───────────────────────────────────────────

# Myst wireguard DNS manager invokes <script-dir>/update-resolv-conf on Unix.
# In containerized namespace-sharing mode, keep DNS managed by Docker.
if [ "${MYST_SKIP_UPDATE_RESOLV_CONF:-false}" = "true" ]; then
  printf '%s\n' '#!/bin/sh' 'exit 0' > "${OS_DIR_CONFIG}/update-resolv-conf"
  chmod +x "${OS_DIR_CONFIG}/update-resolv-conf"
fi

# Capture the container-engine bridge gateway before removing its default
# route so the kill-switch and route exemptions keep using the host-side path
# only. Podman omits `dev eth0` from a device-filtered route listing, so find
# values by token rather than by field position.
_default_route="$(ip -4 route show default dev eth0 2>/dev/null | awk '/default/ {print; exit}')"
DOCKER_BRIDGE_GW="$(printf '%s\n' "${_default_route}" | awk '{for (i=1; i<NF; i++) if ($i == "via") {print $(i+1); exit}}')"
if [ -n "${DOCKER_BRIDGE_GW:-}" ]; then
  DOCKER_BRIDGE_DEV="eth0"
else
  _default_route="$(ip -4 route show default 2>/dev/null | awk '/default/ {print; exit}')"
  DOCKER_BRIDGE_GW="$(printf '%s\n' "${_default_route}" | awk '{for (i=1; i<NF; i++) if ($i == "via") {print $(i+1); exit}}')"
  DOCKER_BRIDGE_DEV="$(printf '%s\n' "${_default_route}" | awk '{for (i=1; i<NF; i++) if ($i == "dev") {print $(i+1); exit}}')"
fi
unset _default_route

# Start daemon in consumer-only mode (no provider services) with optional
# quality telemetry disabled. Provider discovery, NAT traversal, connection
# assistance, registration, and payment traffic remain enabled.
# Disable LAN discovery to avoid mDNS/Bonjour conflicts in host-network tests.
set -- --local-service-discovery=false --consumer --quality.type=none
if [ -n "${MYST_VPN_WIREGUARD_MTU:-}" ]; then
  echo "Using WireGuard MTU override: ${MYST_VPN_WIREGUARD_MTU}"
  set -- "$@" --wireguard.mtu="${MYST_VPN_WIREGUARD_MTU}"
fi
set -- "$@" daemon
/usr/local/bin/docker-entrypoint.sh "$@" &
svc_pid="$!"

. /usr/local/bin/myst-route-reconciliation.sh

(
  while true; do
    apply_wireguard_mtu
    apply_route_exemptions
    sleep 20
  done
) &
route_fix_pid="$!"

echo "Waiting for TequilAPI to be reachable..."
# Wait for TequilAPI to be reachable (myst CLI uses TequilAPI on 127.0.0.1:4050).
until $MYST connection info >/dev/null 2>&1; do
  sleep 2
done

# Accept consumer/provider terms on first run via the supported `myst cli` flag.
myst_cli identities list >/dev/null 2>&1 || true

echo "TequilAPI is ready."

# Standalone signup/payment containers deliberately stop here.  The host-side
# helper is the sole owner of identity creation, registration, and funding
# mutations so two independent processes can never race an order creation.
# Keep the daemon alive across an arbitrarily long user payment pause.
case "${MYST_SETUP_ONLY:-false}" in
  true)
    echo "MYST_SETUP_ONLY=true: Myst daemon is ready for explicit signup/payment commands."
    set +e
    wait "$svc_pid"
    svc_status="$?"
    set -e
    stop_children
    exit "${svc_status}"
    ;;
  false) ;;
  *)
    echo "ERROR: MYST_SETUP_ONLY must be true or false." >&2
    exit 1
    ;;
esac

strip_ansi() {
  sed -E 's/\x1B\[[0-9;]*[A-Za-z]//g'
}

# The pinned CLI renders each identity as an optional `[+]` marker followed by
# one address on its own line. Do not accept an arbitrary address embedded in
# diagnostics as the wallet selection.
IDS="$(myst_cli identities list 2>/dev/null \
  | strip_ansi \
  | grep -E '^[[:space:]]*(\[\+\][[:space:]]*)?0x[0-9a-fA-F]{40}[[:space:]]*$' \
  | grep -Eo '0x[0-9a-fA-F]{40}' || true)"
ID=""
IDENTITY_COUNT="$(printf '%s\n' "$IDS" | awk 'NF { count++ } END { print count + 0 }')"
if [ -n "${MYST_VPN_IDENTITY:-}" ]; then
  if ! printf '%s\n' "${MYST_VPN_IDENTITY}" | grep -Eq '^0x[0-9a-fA-F]{40}$'; then
    echo "WARNING: MYST_VPN_IDENTITY is malformed; explicit signup repair is required."
  else
    ID="$(printf '%s\n' "$IDS" | awk -v wanted="${MYST_VPN_IDENTITY}" \
      'tolower($0) == tolower(wanted) { print; exit }')"
    if [ -z "$ID" ]; then
      echo "WARNING: MYST_VPN_IDENTITY does not select an available identity; explicit signup repair is required."
    fi
  fi
elif [ "$IDENTITY_COUNT" -eq 1 ]; then
  ID="$IDS"
elif [ "$IDENTITY_COUNT" -gt 1 ]; then
  echo "WARNING: Multiple Myst identities exist; set MYST_VPN_IDENTITY explicitly."
fi

if [ -n "$ID" ]; then
  if ! myst_cli identities unlock "$ID"; then
    echo "WARNING: Could not unlock Myst identity $ID; explicit signup repair is required."
  fi
else
  echo "WARNING: No usable Myst identity is available; run make vpn-signup-orderform or make vpn-signup-blockchain."
fi

registration_status() {
  myst_cli identities get "$ID" 2>/dev/null \
    | strip_ansi \
    | grep -i 'Registration Status' | grep -oE '[A-Za-z]+$' | tr '[:upper:]' '[:lower:]' || true
}

identity_balance() {
  myst_cli identities get "$ID" 2>/dev/null \
    | strip_ansi \
    | grep -i 'Balance:' | grep -oE '[0-9]+(\.[0-9]+)?' | head -n1 || true
}

REG_STATUS=""
BALANCE=""
if [ -n "$ID" ]; then
  REG_STATUS="$(registration_status)"
  BALANCE="$(identity_balance)"
fi
REG_FINAL_STATUS="${REG_STATUS:-unknown}"
echo "Identity: $ID  |  Balance: ${BALANCE:-unknown} MYST"

if [ "${REG_FINAL_STATUS}" != "registered" ]; then
  echo "WARNING: Registration status is ${REG_FINAL_STATUS}; explicit signup repair is required."
fi

connection_is_up() {
  $MYST connection info 2>/dev/null \
    | grep -Eiq '"status"[[:space:]]*:[[:space:]]*"Connected"|Status:[[:space:]]*Connected'
}

ts_utc() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

log_with_ts() {
  echo "[$(ts_utc)] $*"
}

connection_status_line() {
  $MYST connection info 2>/dev/null \
    | strip_ansi \
    | grep -E 'Status:|"status"' \
    | head -n1 || true
}

connection_is_up_stable() {
  # Use multiple probes to avoid transient false negatives while TequilAPI settles.
  _probe_connected=0
  _probe=1
  while [ "$_probe" -le 3 ]; do
    if connection_is_up; then
      _probe_connected="$(( _probe_connected + 1 ))"
    fi
    if [ "$_probe" -lt 3 ]; then
      sleep 1
    fi
    _probe="$(( _probe + 1 ))"
  done
  [ "$_probe_connected" -ge 2 ]
}

wait_for_connected() {
  _status_timeout="${MYST_CONNECT_STATUS_TIMEOUT:-30}"
  case "$_status_timeout" in ''|0) _status_timeout=30 ;; esac

  log_with_ts "Waiting up to ${_status_timeout}s for Connected status."
  _elapsed=0
  while [ "$_elapsed" -lt "$_status_timeout" ]; do
    if connection_is_up; then
      _status_line="$(connection_status_line)"
      log_with_ts "Connected status observed after ${_elapsed}s: ${_status_line:-unknown}"
      return 0
    fi

    # Emit periodic diagnostics so we can trace status-check drift/regressions.
    if [ "$(( _elapsed % 6 ))" -eq 0 ]; then
      _status_line="$(connection_status_line)"
      log_with_ts "Still waiting for Connected status at ${_elapsed}s: ${_status_line:-unknown}"
    fi

    sleep 2
    _elapsed="$(( _elapsed + 2 ))"
  done

  _status_line="$(connection_status_line)"
  log_with_ts "Connected status not observed within ${_status_timeout}s. Last status: ${_status_line:-unknown}"
  return 1
}

connect_one_attempt() {
  _attempt_num="${1:-1}"
  _svc="${MYST_SERVICE_TYPE:-wireguard}"
  _loc="${MYST_LOCATION_TYPE:-residential}"
  _up_timeout="${MYST_CONNECT_UP_TIMEOUT:-45}"
  case "$_up_timeout" in ''|0) _up_timeout=45 ;; esac

  run_connection_up() {
    # shellcheck disable=SC2039
    set +e
    _connect_out="$($@ 2>&1)"
    _connect_rc=$?
    set -e

    if [ -n "${_connect_out:-}" ]; then
      printf '%s\n' "${_connect_out}"
    fi

    if printf '%s' "${_connect_out:-}" | grep -Eiq 'err_connection_already_exists|connection already exists'; then
      # If daemon says connection already exists, avoid any further retry/provider churn.
      log_with_ts "connect command returned already_exists; treating as successful no-op and stopping retries for this cycle."
      return 0
    fi

    if [ "$_connect_rc" -eq 0 ]; then
      if wait_for_connected; then
        return 0
      fi
      log_with_ts "connect command exited 0 but Connected status was not observed before timeout."
      return 1
    fi

    if [ "$_connect_rc" -eq 124 ]; then
      log_with_ts "connect command timed out after ${_up_timeout}s (exit 124)."
    else
      log_with_ts "connect command failed with exit ${_connect_rc}."
    fi

    if connection_is_up_stable; then
      log_with_ts "Connected status became stable after connect command failure; treating as success."
      return 0
    fi
    return 1
  }

  # Avoid tearing down an already-established tunnel during retry cycles.
  if connection_is_up_stable; then
    log_with_ts "Connection is already established before attempting connect."
    return 0
  fi

  if [ -n "${MYST_VPN_PREFERRED_PROVIDER_IDS:-}" ]; then
    OLDIFS="$IFS"
    IFS=','
    _provider_list=""
    _provider_count=0
    for _provider in ${MYST_VPN_PREFERRED_PROVIDER_IDS}; do
      _provider="$(printf '%s' "${_provider}" | xargs)"
      [ -n "${_provider}" ] || continue
      _provider_count="$(( _provider_count + 1 ))"
      if [ -z "${_provider_list}" ]; then
        _provider_list="${_provider}"
      else
        _provider_list="${_provider_list}\n${_provider}"
      fi
    done
    IFS="$OLDIFS"

    if [ "$_provider_count" -eq 0 ]; then
      log_with_ts "MYST_VPN_PREFERRED_PROVIDER_IDS was set but no valid provider IDs were parsed."
      return 1
    fi

    # Strict no-fan-out policy: exactly one provider attempt per retry cycle.
    _selected_index="$(( (( _attempt_num - 1 ) % _provider_count) + 1 ))"
    _selected_provider="$(printf '%b' "${_provider_list}" | sed -n "${_selected_index}p")"
    log_with_ts "Pinned providers enabled (count=${_provider_count}); attempt ${_attempt_num} selecting provider ${_selected_index}/${_provider_count}: ${_selected_provider}"

    if connection_is_up_stable; then
      log_with_ts "Connection is already established before provider ${_selected_provider}; skipping down/up."
      return 0
    fi

    $MYST connection down >/dev/null 2>&1 || true

    if run_connection_up timeout ${_up_timeout} myst \
      --config-dir="${OS_DIR_DATA}" \
      --script-dir="${OS_DIR_CONFIG}" \
      --data-dir="${OS_DIR_DATA}" \
      --runtime-dir="${OS_DIR_RUN}" \
      connection up "${_selected_provider}" --service-type="${_svc}" --location-type="${_loc}"; then
      return 0
    fi

    log_with_ts "Pinned provider ${_selected_provider} did not reach stable Connected status in attempt ${_attempt_num}."
    return 1
  fi

  log_with_ts "Connection attempt using provider selection filters (provider_id=auto country=${MYST_COUNTRY:-any})"

  if ! connection_is_up_stable; then
    $MYST connection down >/dev/null 2>&1 || true
  else
    log_with_ts "Connection is already established before auto-selection connect; skipping down/up."
    return 0
  fi

  if [ -n "${MYST_COUNTRY:-}" ]; then
    if run_connection_up timeout ${_up_timeout} myst \
      --config-dir="${OS_DIR_DATA}" \
      --script-dir="${OS_DIR_CONFIG}" \
      --data-dir="${OS_DIR_DATA}" \
      --runtime-dir="${OS_DIR_RUN}" \
      connection up --country="${MYST_COUNTRY}" --service-type="${_svc}" --location-type="${_loc}"; then
      return 0
    fi
  else
    if run_connection_up timeout ${_up_timeout} myst \
      --config-dir="${OS_DIR_DATA}" \
      --script-dir="${OS_DIR_CONFIG}" \
      --data-dir="${OS_DIR_DATA}" \
      --runtime-dir="${OS_DIR_RUN}" \
      connection up --service-type="${_svc}" --location-type="${_loc}"; then
      return 0
    fi
  fi

  if connection_is_up_stable; then
    log_with_ts "Connected status became stable after provider auto-selection failure; treating as success."
    return 0
  fi
  return 1
}

if [ "${MYST_AUTO_CONNECT:-true}" = "true" ] && [ -n "$ID" ]; then
  if [ "${REG_FINAL_STATUS:-unknown}" != "registered" ]; then
    log_with_ts "Skipping auto-connect: registration status is ${REG_FINAL_STATUS:-unknown}."
  else
  _max_attempts="${MYST_CONNECT_MAX_ATTEMPTS:-6}"
  _retry_interval="${MYST_CONNECT_RETRY_INTERVAL:-10}"
  case "$_max_attempts" in ''|0) _max_attempts=1 ;; esac
  case "$_retry_interval" in ''|0) _retry_interval=10 ;; esac

  _attempt=1
  while [ "$_attempt" -le "$_max_attempts" ]; do
    if [ -n "${MYST_VPN_PREFERRED_PROVIDER_IDS:-}" ]; then
      log_with_ts "Connect attempt $_attempt/$_max_attempts mode=pinned-single-provider"
    else
      log_with_ts "Connect attempt $_attempt/$_max_attempts mode=auto-provider-selection"
    fi
    if connect_one_attempt "$_attempt"; then
      # Apply once immediately after success; the single background owner
      # continues repairing route or MTU drift within 20 seconds thereafter.
      apply_wireguard_mtu
      apply_route_exemptions
      log_with_ts "Connection established on attempt $_attempt"
      break
    fi

    if [ "$_attempt" -lt "$_max_attempts" ]; then
      log_with_ts "Connect attempt $_attempt failed, retrying in ${_retry_interval}s"
      sleep "$_retry_interval"
    fi
    _attempt="$(( _attempt + 1 ))"
  done

  if ! connection_is_up; then
    log_with_ts "WARNING: All connect attempts failed during startup; healthcheck will continue evaluating readiness."
  fi

  fi
fi

# Keep service in foreground; Myst kill-switch stays enabled unless explicitly disabled.
set +e
wait "$svc_pid"
svc_status="$?"
set -e
stop_children
exit "${svc_status}"
