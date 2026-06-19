#!/bin/sh
# ══════════════════════════════════════════════════════════════════════════════
# myst-vpn-cli.sh — Helper for Mysterium VPN signup, order status, and balance
# ══════════════════════════════════════════════════════════════════════════════
#
# Subcommands:
#   signup        — Wait for daemon, show/create order, print payment URL
#   orderstatus   — Show identity, balance, registration, all orders + payment URLs
#   balance       — Quick identity + balance check
#
# Usage (from Makefile):
#   ./myst/myst-vpn-cli.sh signup
#   ./myst/myst-vpn-cli.sh orderstatus
#   ./myst/myst-vpn-cli.sh balance
#
# Environment:
#   CONTAINER_BIN   — docker or podman (default: docker)
#   CONTAINER_NAME  — myst container name (default: myst-client-vpn)
#   MYST_ORDER_*    — order creation params (read from .env.wrapper via Makefile)
#
# This script runs `docker exec` against the running myst container.
# It does NOT start or stop containers — that's the Makefile's job.
# ══════════════════════════════════════════════════════════════════════════════
set -eu

CONTAINER_BIN="${CONTAINER_BIN:-docker}"
CONTAINER_NAME="${CONTAINER_NAME:-myst-client-vpn}"

# Myst CLI config flags (must match myst-client-entrypoint.sh)
OS_DIR_DATA="/var/lib/mysterium-node"
OS_DIR_RUN="/var/run/mysterium-node"
OS_DIR_CONFIG="/etc/mysterium-node"
MYST_FLAGS="--config-dir=${OS_DIR_DATA} --script-dir=${OS_DIR_CONFIG} --data-dir=${OS_DIR_DATA} --runtime-dir=${OS_DIR_RUN}"

# ── Helpers ───────────────────────────────────────────────────────────────────

# Run a myst CLI command inside the container.
myst_exec() {
  "$CONTAINER_BIN" exec "$CONTAINER_NAME" myst $MYST_FLAGS "$@"
}

# Run a myst cli subcommand (adds --agreed-terms-and-conditions).
myst_cli() {
  myst_exec cli --agreed-terms-and-conditions "$@"
}

# Check if the container is running.
container_running() {
  "$CONTAINER_BIN" inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null | grep -q true
}

# Wait for TequilAPI to be reachable (max ~120s).
wait_for_tequilapi() {
  echo "Waiting for Myst daemon (TequilAPI) to be ready..."
  _elapsed=0
  while [ "$_elapsed" -lt 120 ]; do
    if myst_exec connection info >/dev/null 2>&1; then
      echo "TequilAPI is ready."
      return 0
    fi
    sleep 3
    _elapsed="$(( _elapsed + 3 ))"
    # Print progress every 15s
    if [ "$(( _elapsed % 15 ))" -eq 0 ]; then
      echo "  ...still waiting (${_elapsed}s)"
    fi
  done
  echo "ERROR: TequilAPI did not become reachable within 120s."
  echo "       Check container logs: $CONTAINER_BIN logs $CONTAINER_NAME"
  return 1
}

# Strip ANSI escape codes.
strip_ansi() {
  sed -E 's/\x1B\[[0-9;]*[A-Za-z]//g'
}

# Get the first identity address (0x...).
get_identity() {
  myst_cli identities list 2>/dev/null \
    | grep -Eo '0x[0-9a-fA-F]{40}' \
    | head -n1 || true
}

# Get registration status (lowercase).
get_registration_status() {
  _id="$1"
  myst_cli identities get "$_id" 2>/dev/null \
    | strip_ansi \
    | grep -i 'Registration Status' \
    | grep -oE '[A-Za-z]+$' \
    | tr '[:upper:]' '[:lower:]' || true
}

# Get balance (numeric string, may be empty).
get_balance() {
  _id="$1"
  myst_cli identities get "$_id" 2>/dev/null \
    | strip_ansi \
    | grep -i 'Balance:' \
    | grep -oE '[0-9]+(\.[0-9]+)?' \
    | head -n1 || true
}

# Check if balance is zero (or empty).
balance_is_zero() {
  case "${1:-0}" in
    ''|0|0.0|0.00|0.000|0.0000|0.00000|0.000000) return 0 ;;
    *) return 1 ;;
  esac
}

# Extract payment URL from order output.
# The myst CLI prints "Data: {json}" where the JSON may contain a URL.
# We try python3 first, then fall back to grep for any https URL.
extract_payment_url() {
  _input="$1"
  # Try python3 JSON parsing for common field names
  _url="$(printf '%s' "$_input" | "$CONTAINER_BIN" exec -i "$CONTAINER_NAME" python3 -c '
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
# Deep search for any URL value
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
  if [ -n "$_url" ]; then
    printf '%s' "$_url"
    return 0
  fi
  # Fallback: grep for any https URL in the raw text
  _url="$(printf '%s' "$_input" | grep -oE 'https://[^"[:space:]]+' | head -n1 || true)"
  if [ -n "$_url" ]; then
    printf '%s' "$_url"
    return 0
  fi
  return 1
}

# Print a banner line.
banner() {
  printf '═══════════════════════════════════════════════════════════\n'
}

# ── Subcommand: balance ───────────────────────────────────────────────────────

cmd_balance() {
  if ! container_running; then
    echo "ERROR: Container '$CONTAINER_NAME' is not running."
    echo "       Run 'make vpn-signup' or 'make up-lite' first."
    exit 1
  fi

  ID="$(get_identity)"
  if [ -z "$ID" ]; then
    echo "No Myst identity found. The container may still be initializing."
    echo "Wait a moment and try again."
    exit 1
  fi

  BALANCE="$(get_balance "$ID")"
  REG_STATUS="$(get_registration_status "$ID")"

  banner
  echo "Myst VPN Balance"
  banner
  echo "  Identity:    $ID"
  echo "  Balance:     ${BALANCE:-0} MYST"
  echo "  Registration: ${REG_STATUS:-unknown}"
  banner

  if balance_is_zero "$BALANCE"; then
    echo "Balance is zero. Run 'make vpn-signup' to create a payment order."
  else
    echo "Balance is funded. You can start the full stack with 'make up-lite'."
  fi
}

# ── Subcommand: orderstatus ───────────────────────────────────────────────────

cmd_orderstatus() {
  if ! container_running; then
    echo "ERROR: Container '$CONTAINER_NAME' is not running."
    echo "       Run 'make vpn-signup' or 'make up-lite' first."
    exit 1
  fi

  ID="$(get_identity)"
  if [ -z "$ID" ]; then
    echo "No Myst identity found. The container may still be initializing."
    echo "Wait a moment and try again."
    exit 1
  fi

  BALANCE="$(get_balance "$ID")"
  REG_STATUS="$(get_registration_status "$ID")"

  banner
  echo "Myst VPN Order Status"
  banner
  echo "  Identity:     $ID"
  echo "  Balance:      ${BALANCE:-0} MYST"
  echo "  Registration: ${REG_STATUS:-unknown}"
  banner

  # List all orders
  echo ""
  echo "Payment Orders:"
  ORDERS_OUT="$(myst_cli orders get-all "$ID" 2>&1 || true)"
  echo "$ORDERS_OUT" | sed 's/^/  /'

  # Check if there are any orders (not "No orders found")
  if printf '%s' "$ORDERS_OUT" | grep -qi 'no orders found'; then
    echo ""
    echo "No payment orders exist yet."
  else
    # Try to get detailed info for each order to extract payment URLs
    # Parse order IDs from the get-all output
    ORDER_IDS="$(printf '%s' "$ORDERS_OUT" | grep -oE "Order ID '[^']+'" | sed "s/Order ID '//; s/'//" || true)"
    if [ -n "$ORDER_IDS" ]; then
      echo ""
      echo "Order Details:"
      for OID in $ORDER_IDS; do
        echo ""
        echo "  Order: $OID"
        ORDER_DETAIL="$(myst_cli orders get "$ID" "$OID" 2>&1 || true)"
        echo "$ORDER_DETAIL" | sed 's/^/    /'

        # Extract payment URL from the Data: line
        DATA_LINE="$(printf '%s' "$ORDER_DETAIL" | grep -i '^.*Data:' || true)"
        if [ -n "$DATA_LINE" ]; then
          # Get the JSON part after "Data:"
          JSON_PART="$(printf '%s' "$DATA_LINE" | sed 's/^.*Data:[[:space:]]*//' || true)"
          PAY_URL="$(extract_payment_url "$JSON_PART" || true)"
          if [ -n "$PAY_URL" ]; then
            echo ""
            banner
            echo "PAYMENT URL: $PAY_URL"
            banner
          fi
        fi
      done
    fi
  fi

  echo ""
  banner
  if balance_is_zero "$BALANCE"; then
    echo "Balance is still zero."
    echo "Pay at the URL above, then run 'make vpn-orderstatus' again."
    echo "Once funded, run 'make up-lite' to start the full stack."
  else
    echo "Balance is funded (${BALANCE} MYST)!"
    echo "Run 'make up-lite' (or 'make up-full') to start the full stack."
  fi
  banner
}

# ── Subcommand: signup ────────────────────────────────────────────────────────

cmd_signup() {
  if ! container_running; then
    echo "ERROR: Container '$CONTAINER_NAME' is not running."
    echo "       The Makefile should start it before calling this script."
    exit 1
  fi

  # Wait for daemon to be ready
  wait_for_tequilapi || exit 1

  # Get identity
  ID="$(get_identity)"
  if [ -z "$ID" ]; then
    echo "No Myst identity found. Creating one..."
    myst_cli identities new >/dev/null 2>&1 || true
    sleep 2
    ID="$(get_identity)"
  fi

  if [ -z "$ID" ]; then
    echo "ERROR: Could not create or find a Myst identity."
    echo "       Check container logs: $CONTAINER_BIN logs $CONTAINER_NAME"
    exit 1
  fi

  # Unlock identity
  myst_cli identities unlock "$ID" >/dev/null 2>&1 || true

  # Check registration
  REG_STATUS="$(get_registration_status "$ID")"
  BALANCE="$(get_balance "$ID")"

  banner
  echo "Myst VPN Signup"
  banner
  echo "  Identity:     $ID"
  echo "  Registration: ${REG_STATUS:-unknown}"
  echo "  Balance:      ${BALANCE:-0} MYST"
  banner

  # If already funded, tell user to proceed
  if ! balance_is_zero "$BALANCE"; then
    echo ""
    echo "Your wallet is already funded with ${BALANCE} MYST."
    echo "You can start the full stack now:"
    echo ""
    echo "  make up-lite    (or make up-full)"
    echo ""
    exit 0
  fi

  # Check for existing orders
  echo ""
  echo "Checking for existing payment orders..."
  ORDERS_OUT="$(myst_cli orders get-all "$ID" 2>&1 || true)"

  HAS_UNPAID_ORDER=false
  if ! printf '%s' "$ORDERS_OUT" | grep -qi 'no orders found'; then
    echo "$ORDERS_OUT" | sed 's/^/  /'
    # Check if any order is in an unpaid state (initial, new)
    if printf '%s' "$ORDERS_OUT" | grep -qiE "state: '(initial|new)'"; then
      HAS_UNPAID_ORDER=true
    fi
  else
    echo "  No existing orders found."
  fi

  if [ "$HAS_UNPAID_ORDER" = "true" ]; then
    echo ""
    echo "An unpaid order already exists. Showing details..."
    # Get the first unpaid order ID
    ORDER_IDS="$(printf '%s' "$ORDERS_OUT" | grep -oE "Order ID '[^']+'" | sed "s/Order ID '//; s/'//" || true)"
    for OID in $ORDER_IDS; do
      ORDER_DETAIL="$(myst_cli orders get "$ID" "$OID" 2>&1 || true)"
      # Check if this order is unpaid
      if printf '%s' "$ORDER_DETAIL" | grep -qiE "state: '(initial|new)'"; then
        echo ""
        echo "$ORDER_DETAIL" | sed 's/^/  /'
        DATA_LINE="$(printf '%s' "$ORDER_DETAIL" | grep -i '^.*Data:' || true)"
        if [ -n "$DATA_LINE" ]; then
          JSON_PART="$(printf '%s' "$DATA_LINE" | sed 's/^.*Data:[[:space:]]*//' || true)"
          PAY_URL="$(extract_payment_url "$JSON_PART" || true)"
          if [ -n "$PAY_URL" ]; then
            echo ""
            banner
            echo "PAYMENT URL: $PAY_URL"
            banner
          fi
        fi
        break
      fi
    done

    echo ""
    banner
    echo "Pay at the URL above to fund your wallet."
    echo "Then run: make vpn-orderstatus"
    echo "Once funded: make up-lite"
    banner
    exit 0
  fi

  # No unpaid order — create a new one
  echo ""
  echo "Creating a new payment order..."

  # Use env vars if set, otherwise use defaults
  ORDER_AMOUNT="${MYST_ORDER_AMOUNT:-100}"
  ORDER_CURRENCY="${MYST_ORDER_CURRENCY:-}"
  ORDER_GATEWAY="${MYST_ORDER_GATEWAY:-}"
  ORDER_COUNTRY="${MYST_ORDER_COUNTRY:-${MYST_COUNTRY:-US}}"
  ORDER_GATEWAY_DATA="${MYST_ORDER_GATEWAY_DATA:-custom_id=mysterium-onyx}"

  # If gateway or currency not set, discover from gateways list
  if [ -z "$ORDER_GATEWAY" ] || [ -z "$ORDER_CURRENCY" ]; then
    echo "Discovering available payment gateways..."
    GATEWAYS_OUT="$(myst_cli orders gateways 2>&1 || true)"
    echo "$GATEWAYS_OUT" | sed 's/^/  /'

    if [ -z "$ORDER_GATEWAY" ]; then
      # Extract first gateway name
      ORDER_GATEWAY="$(printf '%s' "$GATEWAYS_OUT" | grep -i 'Gateway:' | head -n1 | sed 's/^.*Gateway:[[:space:]]*//' | tr -d '[:space:]' || true)"
    fi
    if [ -z "$ORDER_CURRENCY" ]; then
      # Extract first currency from supported currencies
      ORDER_CURRENCY="$(printf '%s' "$GATEWAYS_OUT" | grep -i 'Supported currencies:' | head -n1 | sed 's/^.*Supported currencies:[[:space:]]*//' | cut -d',' -f1 | tr -d '[:space:]' || true)"
    fi
  fi

  if [ -z "$ORDER_GATEWAY" ] || [ -z "$ORDER_CURRENCY" ]; then
    echo "ERROR: Could not determine payment gateway or currency."
    echo "       Set MYST_ORDER_GATEWAY and MYST_ORDER_CURRENCY in .env.wrapper"
    echo "       Available gateways:"
    myst_cli orders gateways 2>&1 | sed 's/^/         /' || true
    exit 1
  fi

  echo "  Amount:   $ORDER_AMOUNT MYST"
  echo "  Pay with: $ORDER_CURRENCY"
  echo "  Gateway:  $ORDER_GATEWAY"
  echo "  Country:  $ORDER_COUNTRY"
  echo ""

  # Create the order
  set +e
  CREATE_OUT="$(myst_cli orders create \
    "$ID" \
    "$ORDER_AMOUNT" \
    "$ORDER_CURRENCY" \
    "$ORDER_GATEWAY" \
    "$ORDER_COUNTRY" \
    "$ORDER_GATEWAY_DATA" 2>&1)"
  CREATE_RC=$?
  set -e

  if [ -n "$CREATE_OUT" ]; then
    echo "$CREATE_OUT" | sed 's/^/  /'
  fi

  if [ "$CREATE_RC" -ne 0 ]; then
    echo ""
    echo "ERROR: Order creation failed (exit $CREATE_RC)."
    echo "       Check container logs: $CONTAINER_BIN logs $CONTAINER_NAME"
    exit 1
  fi

  # Extract payment URL from the create output
  DATA_LINE="$(printf '%s' "$CREATE_OUT" | grep -i '^.*Data:' || true)"
  if [ -n "$DATA_LINE" ]; then
    JSON_PART="$(printf '%s' "$DATA_LINE" | sed 's/^.*Data:[[:space:]]*//' || true)"
    PAY_URL="$(extract_payment_url "$JSON_PART" || true)"
    if [ -n "$PAY_URL" ]; then
      echo ""
      banner
      echo "PAYMENT URL: $PAY_URL"
      banner
    else
      echo ""
      echo "NOTE: Could not extract payment URL from order response."
      echo "      Check full order details above or container logs."
    fi
  fi

  echo ""
  banner
  echo "Order created successfully!"
  echo ""
  echo "Next steps:"
  echo "  1. Pay at the URL above using $ORDER_CURRENCY"
  echo "  2. Check payment:  make vpn-orderstatus"
  echo "  3. Start full stack: make up-lite"
  echo ""
  echo "If the order expires, run 'make vpn-signup' again to create a new one."
  banner
}

# ── Main ──────────────────────────────────────────────────────────────────────

case "${1:-}" in
  signup)
    cmd_signup
    ;;
  orderstatus)
    cmd_orderstatus
    ;;
  balance)
    cmd_balance
    ;;
  *)
    echo "Usage: $0 {signup|orderstatus|balance}"
    echo ""
    echo "  signup       — Wait for daemon, show/create order, print payment URL"
    echo "  orderstatus  — Show identity, balance, all orders + payment URLs"
    echo "  balance      — Quick identity + balance check"
    exit 1
    ;;
esac
