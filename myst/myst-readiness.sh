#!/bin/sh
set -eu

case "${MYST_VPN_ENABLED:-true}" in
  true|false) ;;
  *)
    echo "VPN readiness failed: MYST_VPN_ENABLED must be true or false" >&2
    exit 1
    ;;
esac

if [ "${MYST_VPN_ENABLED:-true}" = "false" ]; then
  if ip link show myst0 >/dev/null 2>&1; then
    echo "VPN-disabled readiness failed: unexpected myst0 interface" >&2
    exit 1
  fi

  # Docker assigns ethN names from network-attachment order. In the full
  # topology the Internet-capable default network is not necessarily eth0,
  # so validate the selected IPv4 default route rather than an interface name.
  default_dev="$(
    ip -4 route show default 2>/dev/null \
      | awk '$1 == "default" {
          for (i = 1; i <= NF; i++) {
            if ($i == "dev" && $(i + 1) != "myst0") {
              print $(i + 1)
              exit
            }
          }
        }'
  )"
  if [ -z "${default_dev}" ]; then
    echo "VPN-disabled readiness failed: no non-myst0 IPv4 default route" >&2
    exit 1
  fi
  if ! ip link show dev "${default_dev}" >/dev/null 2>&1; then
    echo "VPN-disabled readiness failed: default interface ${default_dev} is missing" >&2
    exit 1
  fi
  if ! ip -4 -o addr show dev "${default_dev}" scope global | grep -q .; then
    echo "VPN-disabled readiness failed: default interface ${default_dev} has no global IPv4 address" >&2
    exit 1
  fi
  exit 0
fi

wget -Y off -q -T 5 -O - http://127.0.0.1:4050/connection 2>/dev/null \
  | awk '
      { response = response $0 }
      END {
        if (response !~ /^[[:space:]]*\{[[:space:]]*"status"[[:space:]]*:[[:space:]]*"Connected"[[:space:]]*[,}]/ ||
            response !~ /\}[[:space:]]*$/) exit 1
        rest = response
        count = 0
        while (match(rest, /"status"[[:space:]]*:/)) {
          count++
          rest = substr(rest, RSTART + RLENGTH)
        }
        exit count != 1
      }
    '

vpn_cidr="$(
  ip -4 -o addr show dev myst0 scope global 2>/dev/null \
    | awk 'NR == 1 {print $4}'
)"
if [ -z "${vpn_cidr}" ]; then
  echo "VPN readiness failed: myst0 has no global IPv4 address" >&2
  exit 1
fi

local_ip="${vpn_cidr%/*}"

# `route get` is a kernel lookup only: it neither resolves nor sends a packet.
# Require ordinary public traffic to select the tunnel and its current source.
ip -4 route get 198.51.100.1 2>/dev/null \
  | awk -v expected_src="${local_ip}" '
      NR == 1 {
        for (i = 1; i <= NF; i++) {
          if ($i == "dev") dev = $(i + 1)
          if ($i == "src") src = $(i + 1)
        }
      }
      END { exit !(dev == "myst0" && src == expected_src) }
    '
