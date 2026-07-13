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
  ip -4 route show default dev eth0 | grep -q '^default '
  exit 0
fi

myst connection info 2>/dev/null \
  | grep -Eiq '"status"[[:space:]]*:[[:space:]]*"Connected"|Status:[[:space:]]*Connected'

vpn_cidr="$(
  ip -4 -o addr show dev myst0 scope global 2>/dev/null \
    | awk 'NR == 1 {print $4}'
)"
if [ -z "${vpn_cidr}" ]; then
  echo "VPN readiness failed: myst0 has no global IPv4 address" >&2
  exit 1
fi

prefix="${vpn_cidr#*/}"
local_ip="${vpn_cidr%/*}"
case "${prefix}" in
  ''|*[!0-9]*)
    echo "VPN readiness failed: invalid myst0 prefix ${prefix}" >&2
    exit 1
    ;;
esac
if [ "${prefix}" -ge 32 ]; then
  echo "VPN readiness failed: myst0 /${prefix} has no provider resolver" >&2
  exit 1
fi

ip -4 route show dev myst0 | grep -q .

# The provider resolver is the first usable IPv4 address of the Myst subnet.
# Query a fixed, non-user hostname from the tunnel address so stale Connected
# state or a dead data plane makes myst-client unhealthy and triggers autoheal.
provider_dns="$({
  awk -v cidr="${vpn_cidr}" 'BEGIN {
    split(cidr, parts, "/"); split(parts[1], octets, ".");
    ip = (((octets[1] * 256) + octets[2]) * 256 + octets[3]) * 256 + octets[4];
    block = 2 ^ (32 - parts[2]); network = int(ip / block) * block + 1;
    printf "%d.%d.%d.%d\n", int(network / 16777216) % 256,
      int(network / 65536) % 256, int(network / 256) % 256, network % 256;
  }'
} 2>/dev/null)"
if [ -z "${provider_dns}" ]; then
  echo "VPN readiness failed: could not derive provider DNS" >&2
  exit 1
fi

dig +time=5 +tries=1 +short -b "${local_ip}" @"${provider_dns}" example.com A \
  | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$'
