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

# Myst wireguard DNS manager invokes <script-dir>/update-resolv-conf on Unix.
# In containerized namespace-sharing mode, keep DNS managed by Docker.
if [ "${MYST_SKIP_UPDATE_RESOLV_CONF:-false}" = "true" ]; then
  printf '%s\n' '#!/bin/sh' 'exit 0' > "${OS_DIR_CONFIG}/update-resolv-conf"
  chmod +x "${OS_DIR_CONFIG}/update-resolv-conf"
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
  _gw="$(ip -4 route show default 2>/dev/null | awk '/default/ {print $3; exit}')"
  _dev="$(ip -4 route show default 2>/dev/null | awk '/default/ {print $5; exit}')"
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

(
  while true; do
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

if [ "$NEEDS_ORDER_CHECK" = "true" ]; then
    # Show any existing orders so the user can track pending payments.
    echo "Checking existing payment orders..."
    EXISTING="$(myst_cli orders get-all "$ID" 2>/dev/null || true)"
    HAS_EXISTING_ORDERS=false
    if [ -n "$EXISTING" ] && ! printf '%s' "$EXISTING" | grep -qi 'no orders found'; then
      HAS_EXISTING_ORDERS=true
    fi
    [ -n "$EXISTING" ] && echo "$EXISTING"

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

wait_for_connected() {
  _status_timeout="${MYST_CONNECT_STATUS_TIMEOUT:-30}"
  case "$_status_timeout" in ''|0) _status_timeout=30 ;; esac

  _elapsed=0
  while [ "$_elapsed" -lt "$_status_timeout" ]; do
    if connection_is_up; then
      return 0
    fi
    sleep 2
    _elapsed="$(( _elapsed + 2 ))"
  done
  return 1
}

ts_utc() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

log_with_ts() {
  echo "[$(ts_utc)] $*"
}

connect_one_attempt() {
  _svc="${MYST_SERVICE_TYPE:-wireguard}"
  _loc="${MYST_LOCATION_TYPE:-residential}"
  _up_timeout="${MYST_CONNECT_UP_TIMEOUT:-45}"
  case "$_up_timeout" in ''|0) _up_timeout=45 ;; esac

  # Avoid tearing down an already-established tunnel during retry cycles.
  if connection_is_up; then
    echo "Connection is already established."
    return 0
  fi

  if [ -n "${MYST_PROVIDER_IDS:-}" ]; then
    OLDIFS="$IFS"
    IFS=','
    for _provider in ${MYST_PROVIDER_IDS}; do
      # A previous provider attempt may have connected asynchronously.
      # Re-check before issuing another `connection up`.
      if connection_is_up; then
        IFS="$OLDIFS"
        echo "Connection is already established."
        return 0
      fi

      _provider="$(printf '%s' "${_provider}" | xargs)"
      [ -n "${_provider}" ] || continue
      log_with_ts "Connection attempt using pinned provider: ${_provider}"
      $MYST connection down >/dev/null 2>&1 || true
      timeout ${_up_timeout} myst \
        --config-dir="${OS_DIR_DATA}" \
        --script-dir="${OS_DIR_CONFIG}" \
        --data-dir="${OS_DIR_DATA}" \
        --runtime-dir="${OS_DIR_RUN}" \
        connection up "${_provider}" --service-type="${_svc}" --location-type="${_loc}" || true
      if wait_for_connected; then
        IFS="$OLDIFS"
        return 0
      fi
    done
    IFS="$OLDIFS"
    return 1
  fi

  log_with_ts "Connection attempt using provider selection filters (provider_id=auto country=${MYST_COUNTRY:-any})"
  $MYST connection down >/dev/null 2>&1 || true
  if [ -n "${MYST_COUNTRY:-}" ]; then
    timeout ${_up_timeout} myst \
      --config-dir="${OS_DIR_DATA}" \
      --script-dir="${OS_DIR_CONFIG}" \
      --data-dir="${OS_DIR_DATA}" \
      --runtime-dir="${OS_DIR_RUN}" \
      connection up --country="${MYST_COUNTRY}" --service-type="${_svc}" --location-type="${_loc}" || true
  else
    timeout ${_up_timeout} myst \
      --config-dir="${OS_DIR_DATA}" \
      --script-dir="${OS_DIR_CONFIG}" \
      --data-dir="${OS_DIR_DATA}" \
      --runtime-dir="${OS_DIR_RUN}" \
      connection up --service-type="${_svc}" --location-type="${_loc}" || true
  fi
  wait_for_connected
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
    log_with_ts "Connect attempt $_attempt/$_max_attempts provider_id=${MYST_PROVIDER_IDS:-auto}"
    if connect_one_attempt; then
      log_with_ts "Connection established on attempt $_attempt provider_id=${MYST_PROVIDER_IDS:-auto}"
      break
    fi

    if [ "$_attempt" -lt "$_max_attempts" ]; then
      log_with_ts "Connect attempt $_attempt failed, retrying in ${_retry_interval}s provider_id=${MYST_PROVIDER_IDS:-auto}"
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
