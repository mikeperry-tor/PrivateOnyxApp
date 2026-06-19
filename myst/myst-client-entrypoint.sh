#!/bin/sh
set -eu

OS_DIR_DATA="/var/lib/mysterium-node"
OS_DIR_RUN="/var/run/mysterium-node"
OS_DIR_CONFIG="/etc/mysterium-node"
MYST="myst --config-dir=${OS_DIR_DATA} --script-dir=${OS_DIR_CONFIG} --data-dir=${OS_DIR_DATA} --runtime-dir=${OS_DIR_RUN}"

myst_cli() {
  $MYST cli --agreed-terms-and-conditions "$@"
}

# Optional LAN access: append common private network CIDRs to route exemptions.
# When ALLOW_LAN_ACCESS=true, LMStudio and other local inference APIs can be
# reached without VPN routing, while remaining connections stay fail-closed.
if [ "${ALLOW_LAN_ACCESS:-false}" = "true" ]; then
  # Common private network ranges per RFC 1918
  LAN_CIDRS="10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
  if [ -n "${MYST_ROUTE_EXEMPT_CIDRS:-}" ]; then
    MYST_ROUTE_EXEMPT_CIDRS="${MYST_ROUTE_EXEMPT_CIDRS},${LAN_CIDRS}"
  else
    MYST_ROUTE_EXEMPT_CIDRS="${LAN_CIDRS}"
  fi
  echo "ALLOW_LAN_ACCESS=true: added LAN CIDRs to route exemptions"
fi

# ── Optional VPN bypass ────────────────────────────────────────────────────
# When MYST_VPN_ENABLED=false, start the daemon in idle mode: no kill-switch
# is armed, no connect attempt is made, and no identity/registration/funding
# flow runs. The container still joins netns-holder so namespace references
# stay valid; traffic egresses directly via the Docker bridge.
if [ "${MYST_VPN_ENABLED:-true}" = "false" ]; then
  echo "MYST_VPN_ENABLED=false: starting myst daemon in idle mode (no kill-switch, no connect)."
  # Defense-in-depth: ensure the daemon does not install firewall/kill-switch
  # rules even if a tunnel is somehow established.
  export MYST_FIREWALL_ENABLED=false
  set -- --local-service-discovery=false --consumer
  if [ -n "${MYST_WIREGUARD_MTU:-}" ]; then
    set -- "$@" --wireguard.mtu="${MYST_WIREGUARD_MTU}"
  fi
  set -- "$@" daemon
  /usr/local/bin/docker-entrypoint.sh "$@" &
  svc_pid="$!"
  trap 'kill "$svc_pid" 2>/dev/null || true; wait "$svc_pid" 2>/dev/null || true' INT TERM
  wait "$svc_pid"
  exit 0
fi

# Myst wireguard DNS manager invokes <script-dir>/update-resolv-conf on Unix.
# In containerized namespace-sharing mode, keep DNS managed by Docker.
if [ "${MYST_SKIP_UPDATE_RESOLV_CONF:-false}" = "true" ]; then
  printf '%s\n' '#!/bin/sh' 'exit 0' > "${OS_DIR_CONFIG}/update-resolv-conf"
  chmod +x "${OS_DIR_CONFIG}/update-resolv-conf"
fi

# Capture the Docker bridge gateway before removing its default route so the
# kill-switch and route exemptions keep using the host-side path only.
DOCKER_BRIDGE_GW="$(ip -4 route show default dev eth0 2>/dev/null | awk '/default/ {print $3; exit}')"
DOCKER_BRIDGE_DEV="$(ip -4 route show default dev eth0 2>/dev/null | awk '/default/ {print $5; exit}')"
if [ -z "${DOCKER_BRIDGE_GW:-}" ] || [ -z "${DOCKER_BRIDGE_DEV:-}" ]; then
  DOCKER_BRIDGE_GW="$(ip -4 route show default 2>/dev/null | awk '/default/ {print $3; exit}')"
  DOCKER_BRIDGE_DEV="$(ip -4 route show default 2>/dev/null | awk '/default/ {print $5; exit}')"
fi

# Start daemon in consumer-only mode (no provider services).
# Disable LAN discovery to avoid mDNS/Bonjour conflicts in host-network tests.
set -- --local-service-discovery=false --consumer
if [ -n "${MYST_WIREGUARD_MTU:-}" ]; then
  echo "Using WireGuard MTU override: ${MYST_WIREGUARD_MTU}"
  set -- "$@" --wireguard.mtu="${MYST_WIREGUARD_MTU}"
fi
set -- "$@" daemon
/usr/local/bin/docker-entrypoint.sh "$@" &
svc_pid="$!"

apply_route_exemptions() {
  _gw="${DOCKER_BRIDGE_GW:-}"
  _dev="${DOCKER_BRIDGE_DEV:-}"
  [ -n "${_gw:-}" ] || return 0
  [ -n "${_dev:-}" ] || return 0

  _hosts="${MYST_ROUTE_EXEMPT_HOSTS:-}"
  if [ -n "${_hosts}" ]; then
    OLDIFS="$IFS"
    IFS=','
    for _host in ${_hosts}; do
      _host="$(printf '%s' "${_host}" | xargs)"
      [ -n "${_host}" ] || continue
      _ips="$(getent ahostsv4 "${_host}" 2>/dev/null | awk '{print $1}' | sort -u || true)"
      if [ -z "${_ips}" ]; then
        echo "Route exemption: no IPv4 resolved for ${_host}"
        continue
      fi
      for _ip in ${_ips}; do
        ip route replace "${_ip}"/32 via "${_gw}" dev "${_dev}" 2>/dev/null || true
        echo "Route exemption: host=${_host} ip=${_ip}/32 via=${_gw} dev=${_dev}"
      done
    done
    IFS="$OLDIFS"
  fi

  _cidrs="${MYST_ROUTE_EXEMPT_CIDRS:-}"
  if [ -n "${_cidrs}" ]; then
    OLDIFS="$IFS"
    IFS=','
    for _cidr in ${_cidrs}; do
      _cidr="$(printf '%s' "${_cidr}" | xargs)"
      [ -n "${_cidr}" ] || continue
      ip route replace "${_cidr}" via "${_gw}" dev "${_dev}" 2>/dev/null || true
      echo "Route exemption: cidr=${_cidr} via=${_gw} dev=${_dev}"
    done
    IFS="$OLDIFS"
  fi
}

apply_wireguard_mtu() {
  [ -n "${MYST_WIREGUARD_MTU:-}" ] || return 0

  if ! ip link show myst0 >/dev/null 2>&1; then
    return 0
  fi

  _current_mtu="$(ip -o link show myst0 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i == "mtu") {print $(i+1); exit}}')"
  if [ -z "${_current_mtu}" ]; then
    return 0
  fi

  if [ "${_current_mtu}" != "${MYST_WIREGUARD_MTU}" ]; then
    if ip link set dev myst0 mtu "${MYST_WIREGUARD_MTU}" >/dev/null 2>&1; then
      echo "WireGuard MTU: set myst0 mtu=${MYST_WIREGUARD_MTU} (was ${_current_mtu})"
    else
      echo "WireGuard MTU: failed to set myst0 mtu=${MYST_WIREGUARD_MTU} (current ${_current_mtu})"
    fi
  fi
}

(
  while true; do
    apply_wireguard_mtu
    apply_route_exemptions
    sleep 20
  done
) &
route_fix_pid="$!"

cleanup() {
  kill "$route_fix_pid" 2>/dev/null || true
  kill "$svc_pid" 2>/dev/null || true
  wait "$route_fix_pid" 2>/dev/null || true
  wait "$svc_pid" 2>/dev/null || true
}
trap cleanup INT TERM

echo "Waiting for TequilAPI to be reachable..."
# Wait for TequilAPI to be reachable (myst CLI uses TequilAPI on 127.0.0.1:4050).
until $MYST connection info >/dev/null 2>&1; do
  sleep 2
done

# Accept consumer/provider terms on first run via the supported `myst cli` flag.
myst_cli identities list >/dev/null 2>&1 || true

echo "TequilAPI is ready."

# The daemon accepts --wireguard.mtu, but some peers/sessions still come up
# with default interface MTU. Enforce requested MTU on the live tunnel.
apply_wireguard_mtu

IDS="$(myst_cli identities list 2>/dev/null | grep -Eo '0x[0-9a-fA-F]{40}' || true)"
if [ -z "$IDS" ]; then
  echo "No Myst identity found in /var/lib/mysterium-node; creating one."
  myst_cli identities new
  IDS="$(myst_cli identities list 2>/dev/null | grep -Eo '0x[0-9a-fA-F]{40}' || true)"
fi

ID="$(printf '%s\n' "$IDS" | head -n1)"
if [ -n "$ID" ]; then
  myst_cli identities unlock "$ID" || true
fi

strip_ansi() {
  sed -E 's/\x1B\[[0-9;]*[A-Za-z]//g'
}

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

# Registration
# Attempt on-chain registration (Mysterium sponsors gas for new identities).
REG_STATUS="$(registration_status)"

if [ "$REG_STATUS" != "registered" ]; then
  submit_registration() {
    if [ -n "${MYST_REFERRAL_TOKEN:-}" ]; then
      if $MYST account register --token="${MYST_REFERRAL_TOKEN}"; then
        echo "Registration request submitted."
      else
        echo "WARNING: Failed to register the identity. Will retry while status remains unregistered."
      fi
    else
      if $MYST account register; then
        echo "Registration request submitted."
      else
        echo "WARNING: Failed to register the identity. Will retry while status remains unregistered."
      fi
    fi
  }

  echo "Identity $ID is not registered (status: ${REG_STATUS:-unknown}). Submitting registration..."
  submit_registration

  # Wait for registration to confirm on-chain.
  # Default is no timeout because confirmation timing depends on
  # transactor/Hermes availability and blockchain conditions.
  _timeout="${MYST_REGISTRATION_TIMEOUT:-0}"
  _retry_interval="${MYST_REGISTRATION_RETRY_INTERVAL:-60}"
  case "$_retry_interval" in
    ''|0) _retry_interval=60 ;;
  esac
  REG_NEEDS_FUNDING=false
  _elapsed=0
  if [ "$_timeout" = "0" ]; then
    echo "Waiting for registration to confirm on-chain (no timeout)."
    while true; do
      REG_STATUS="$(registration_status)"
      echo "Registration status after ${_elapsed}s: ${REG_STATUS:-unknown}"
      [ "$REG_STATUS" = "registered" ] && break

      # `inprogress` with zero balance means registration has started
      # but requires a payment-channel top-up to complete.
      if [ "$REG_STATUS" = "inprogress" ]; then
        REG_BALANCE="$(identity_balance)"
        case "${REG_BALANCE:-0}" in
          ''|0|0.0|0.00|0.000|0.0000|0.00000|0.000000)
            echo "Registration is inprogress with zero balance; proceeding to funding/order creation."
            REG_NEEDS_FUNDING=true
            break
          ;;
        esac
      fi

      if [ "$_elapsed" -gt 0 ] && [ "$(( $_elapsed % _retry_interval ))" -eq 0 ]; then
        case "${REG_STATUS:-unknown}" in
          unregistered|registrationerror|unknown)
            echo "Registration still ${REG_STATUS:-unknown} after ${_elapsed}s; re-submitting registration request..."
            submit_registration
          ;;
        esac
      fi
      sleep 5
      _elapsed="$(( _elapsed + 5 ))"
    done
  else
    echo "Waiting up to ${_timeout}s for registration to confirm on-chain..."
    while [ "$_elapsed" -lt "$_timeout" ]; do
      REG_STATUS="$(registration_status)"
      echo "Registration status after ${_elapsed}s: ${REG_STATUS:-unknown}"
      [ "$REG_STATUS" = "registered" ] && break

      if [ "$REG_STATUS" = "inprogress" ]; then
        REG_BALANCE="$(identity_balance)"
        case "${REG_BALANCE:-0}" in
          ''|0|0.0|0.00|0.000|0.0000|0.00000|0.000000)
            echo "Registration is inprogress with zero balance; proceeding to funding/order creation."
            REG_NEEDS_FUNDING=true
            break
          ;;
        esac
      fi

      if [ "$_elapsed" -gt 0 ] && [ "$(( $_elapsed % _retry_interval ))" -eq 0 ]; then
        case "${REG_STATUS:-unknown}" in
          unregistered|registrationerror|unknown)
            echo "Registration still ${REG_STATUS:-unknown} after ${_elapsed}s; re-submitting registration request..."
            submit_registration
          ;;
        esac
      fi
      sleep 5
      _elapsed="$(( _elapsed + 5 ))"
    done
  fi

  if [ "$REG_STATUS" = "registered" ]; then
    echo "Identity $ID registered successfully."
  elif [ "$REG_NEEDS_FUNDING" = "true" ]; then
    echo "Identity $ID registration is pending funding; top-up flow will run next."
  elif [ "$_timeout" != "0" ]; then
    echo "WARNING: Identity $ID registration did not confirm within ${_timeout}s (status: ${REG_STATUS:-unknown})."
    echo "         Connection attempt will still be made, but may fail until registration completes."
  else
    echo "WARNING: Registration status is still ${REG_STATUS:-unknown}; continuing."
  fi
else
  echo "Identity $ID is already registered."
fi

REG_FINAL_STATUS="${REG_STATUS:-unknown}"

# Balance / Funding
BALANCE="$(identity_balance)"
echo "Identity: $ID  |  Balance: ${BALANCE:-unknown} MYST"

NEEDS_ORDER_CHECK=false
case "${REG_FINAL_STATUS}" in
  registered) ;;
  *)
    NEEDS_ORDER_CHECK=true
    echo "Registration status is ${REG_FINAL_STATUS}; checking payment orders before connect attempts."
  ;;
esac

case "${BALANCE:-0}" in
  ''|0|0.0|0.00|0.000|0.0000|0.00000|0.000000)
    NEEDS_ORDER_CHECK=true
  ;;
esac

# When MYST_SKIP_ORDER_CREATION=true, skip the entire order check/creation
# block. Used by the blockchain signup flow (make vpn-signup-blockchain) where
# the user will transfer $MYST directly on-chain instead of using a payment
# order. The CLI helper (myst-vpn-cli.sh blockchain) handles printing the
# channel address after the entrypoint finishes registration.
if [ "${MYST_SKIP_ORDER_CREATION:-false}" = "true" ]; then
  echo "MYST_SKIP_ORDER_CREATION=true: skipping payment order check/creation."
  NEEDS_ORDER_CHECK=false
fi

# Extract a payment URL from order output (the "Data: {json}" line).
# Tries python3 for structured JSON parsing, falls back to grep for any https URL.
extract_payment_url() {
  _input="$1"
  _url=""
  if command -v python3 >/dev/null 2>&1; then
    _url="$(printf '%s' "$_input" | python3 -c '
import sys, json
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)
for key in ("payment_url", "pay_url", "url", "payment_link", "redirect_url", "checkout_url"):
    val = data.get(key)
    if val and isinstance(val, str) and val.startswith("http"):
        print(val)
        sys.exit(0)
def find_url(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            r = find_url(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_url(v)
            if r:
                return r
    elif isinstance(obj, str) and obj.startswith("http"):
        return obj
    return None
r = find_url(data)
if r:
    print(r)
' 2>/dev/null || true)"
  fi
  if [ -z "$_url" ]; then
    _url="$(printf '%s' "$_input" | grep -oE 'https://[^"[:space:]]+' | head -n1 || true)"
  fi
  [ -n "$_url" ] && printf '%s' "$_url" && return 0
  return 1
}

# Print a payment URL in a prominent, greppable banner.
print_payment_banner() {
  _url="$1"
  printf '%s\n' "═══════════════════════════════════════════════════════════"
  printf 'PAYMENT URL: %s\n' "$_url"
  printf '%s\n' "═══════════════════════════════════════════════════════════"
}

if [ "$NEEDS_ORDER_CHECK" = "true" ]; then
    # Show any existing orders so the user can track pending payments.
    echo "Checking existing payment orders..."
    EXISTING="$(myst_cli orders get-all "$ID" 2>/dev/null || true)"
    HAS_EXISTING_ORDERS=false
    if [ -n "$EXISTING" ] && ! printf '%s' "$EXISTING" | grep -qi 'no orders found'; then
      HAS_EXISTING_ORDERS=true
    fi
    [ -n "$EXISTING" ] && echo "$EXISTING"

    # For existing unpaid orders, try to extract and display the payment URL.
    if [ "$HAS_EXISTING_ORDERS" = "true" ]; then
      _existing_ids="$(printf '%s' "$EXISTING" | grep -oE "Order ID '[^']+'" | sed "s/Order ID '//; s/'//" || true)"
      for _oid in $_existing_ids; do
        _detail="$(myst_cli orders get "$ID" "$_oid" 2>/dev/null || true)"
        _data_line="$(printf '%s' "$_detail" | grep -i '^.*Data:' || true)"
        if [ -n "$_data_line" ]; then
          _json_part="$(printf '%s' "$_data_line" | sed 's/^.*Data:[[:space:]]*//' || true)"
          _pay_url="$(extract_payment_url "$_json_part" || true)"
          if [ -n "$_pay_url" ]; then
            print_payment_banner "$_pay_url"
            break
          fi
        fi
      done
    fi

    # Auto-create a new order if all four required vars are set.
    if [ -n "${MYST_ORDER_AMOUNT:-}" ] && [ -n "${MYST_ORDER_CURRENCY:-}" ] && \
       [ -n "${MYST_ORDER_GATEWAY:-}" ] && [ -n "${MYST_ORDER_COUNTRY:-}" ]; then
      if [ "$HAS_EXISTING_ORDERS" = "true" ]; then
        echo "Skipping auto-create: existing order(s) found."
      else
        echo "Creating order: amount=${MYST_ORDER_AMOUNT} pay_currency=${MYST_ORDER_CURRENCY} gateway=${MYST_ORDER_GATEWAY} country=${MYST_ORDER_COUNTRY}..."
        set +e
        CREATE_OUT="$(myst_cli orders create \
          "$ID" \
          "${MYST_ORDER_AMOUNT}" \
          "${MYST_ORDER_CURRENCY}" \
          "${MYST_ORDER_GATEWAY}" \
          "${MYST_ORDER_COUNTRY}" \
          "${MYST_ORDER_GATEWAY_DATA}" 2>&1)"
        CREATE_RC=$?
        set -e
        if [ -n "$CREATE_OUT" ]; then
          printf '%s\n' "$CREATE_OUT"
        fi
        if [ "$CREATE_RC" -ne 0 ]; then
          echo "WARNING: Order creation failed (exit ${CREATE_RC})."
        else
          # Extract and display the payment URL from the newly created order.
          _data_line="$(printf '%s' "$CREATE_OUT" | grep -i '^.*Data:' || true)"
          if [ -n "$_data_line" ]; then
            _json_part="$(printf '%s' "$_data_line" | sed 's/^.*Data:[[:space:]]*//' || true)"
            _pay_url="$(extract_payment_url "$_json_part" || true)"
            if [ -n "$_pay_url" ]; then
              print_payment_banner "$_pay_url"
            fi
          fi
        fi
      fi
    else
      echo "Set MYST_ORDER_AMOUNT / MYST_ORDER_CURRENCY / MYST_ORDER_GATEWAY / MYST_ORDER_COUNTRY"
      echo "to auto-create a funding order on next start. Available gateways:"
      myst_cli orders gateways 2>/dev/null | sed 's/^/  /' || true
    fi

    # Optionally block until balance is funded before attempting to connect.
    if [ "${MYST_WAIT_FOR_FUNDS:-false}" = "true" ]; then
      echo "MYST_WAIT_FOR_FUNDS=true - polling every 30s until balance > 0..."
      while true; do
        sleep 30
        BALANCE="$(myst_cli identities get "$ID" 2>/dev/null \
          | strip_ansi \
          | grep -i 'Balance:' | grep -oE '[0-9]+(\.[0-9]+)?' | head -n1 || true)"
        echo "Funding check: current balance is ${BALANCE:-0} MYST"
        case "${BALANCE:-0}" in
          ''|0|0.0|0.00|0.000|0.0000|0.00000|0.000000) ;;
          *) echo "Balance is now ${BALANCE} MYST. Proceeding."; break ;;
        esac
      done
    fi
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
      apply_wireguard_mtu
      return 0
    fi

    # Emit periodic diagnostics so we can trace status-check drift/regressions.
    if [ "$(( _elapsed % 6 ))" -eq 0 ]; then
      _status_line="$(connection_status_line)"
      log_with_ts "Still waiting for Connected status at ${_elapsed}s: ${_status_line:-unknown}"
    fi

    apply_wireguard_mtu
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

  if [ -n "${MYST_PROVIDER_IDS:-}" ]; then
    OLDIFS="$IFS"
    IFS=','
    _provider_list=""
    _provider_count=0
    for _provider in ${MYST_PROVIDER_IDS}; do
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
      log_with_ts "MYST_PROVIDER_IDS was set but no valid provider IDs were parsed."
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
      apply_wireguard_mtu
      apply_route_exemptions
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
      apply_wireguard_mtu
      apply_route_exemptions
      return 0
    fi
  else
    if run_connection_up timeout ${_up_timeout} myst \
      --config-dir="${OS_DIR_DATA}" \
      --script-dir="${OS_DIR_CONFIG}" \
      --data-dir="${OS_DIR_DATA}" \
      --runtime-dir="${OS_DIR_RUN}" \
      connection up --service-type="${_svc}" --location-type="${_loc}"; then
      apply_wireguard_mtu
      apply_route_exemptions
      return 0
    fi
  fi

  if connection_is_up_stable; then
    log_with_ts "Connected status became stable after provider auto-selection failure; treating as success."
    apply_wireguard_mtu
    apply_route_exemptions
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
    if [ -n "${MYST_PROVIDER_IDS:-}" ]; then
      log_with_ts "Connect attempt $_attempt/$_max_attempts mode=pinned-single-provider"
    else
      log_with_ts "Connect attempt $_attempt/$_max_attempts mode=auto-provider-selection"
    fi
    if connect_one_attempt "$_attempt"; then
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

  apply_route_exemptions
  fi
fi

# Keep service in foreground; Myst kill-switch stays enabled unless explicitly disabled.
wait "$svc_pid"
