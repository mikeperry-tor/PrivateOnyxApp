#!/bin/sh
set -eu

enabled_raw="${TAILSCALE_FUNNEL_ENABLED:-false}"
enabled="$(printf '%s' "$enabled_raw" | tr '[:upper:]' '[:lower:]')"

case "$enabled" in
  1|true|yes|on)
    ;;
  *)
    echo "tailscale-funnel: disabled (TAILSCALE_FUNNEL_ENABLED=${enabled_raw})."
    exec sleep infinity
    ;;
esac

if [ -z "${TS_AUTHKEY:-}" ]; then
  echo "tailscale-funnel: enabled, but TS_AUTHKEY is empty; idling."
  exec sleep infinity
fi

funnel_https_port="${TAILSCALE_FUNNEL_HTTPS_PORT:-443}"
case "$funnel_https_port" in
  443|8443|10000)
    ;;
  *)
    echo "tailscale-funnel: invalid TAILSCALE_FUNNEL_HTTPS_PORT=$funnel_https_port (allowed: 443, 8443, 10000)."
    exit 1
    ;;
esac

target_host="${TAILSCALE_FUNNEL_TARGET_HOST:-tailscale-frontend-gateway}"
target_port="${TAILSCALE_FUNNEL_TARGET_PORT:-3000}"
serve_config_path="${TS_SERVE_CONFIG:-/etc/tailscale/serve-config.json}"

mkdir -p "$(dirname "$serve_config_path")"
cat >"$serve_config_path" <<EOF
{
  "TCP": {
    "$funnel_https_port": {
      "HTTPS": true
    }
  },
  "Web": {
    "\${TS_CERT_DOMAIN}:$funnel_https_port": {
      "Handlers": {
        "/": {
          "Proxy": "http://$target_host:$target_port"
        }
      }
    }
  },
  "AllowFunnel": {
    "\${TS_CERT_DOMAIN}:$funnel_https_port": true
  }
}
EOF

export TS_SERVE_CONFIG="$serve_config_path"

echo "tailscale-funnel: starting containerboot with userspace networking."
if command -v containerboot >/dev/null 2>&1; then
  exec containerboot
fi

if [ -x /usr/local/bin/containerboot ]; then
  exec /usr/local/bin/containerboot
fi

echo "tailscale-funnel: unable to locate containerboot binary."
exit 1
