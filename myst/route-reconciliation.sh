#!/bin/sh

# Shared by the Myst entrypoint and deterministic tests. The caller supplies
# DOCKER_BRIDGE_GW/DEV plus the documented exemption environment.

route_exemption_matches() {
  _route_target="$1"
  _route_gateway="$2"
  _route_device="$3"
  ip -4 route show exact "${_route_target}" 2>/dev/null | awk \
    -v target="${_route_target}" \
    -v gateway="${_route_gateway}" \
    -v device="${_route_device}" '
      BEGIN {
        host_target = target
        sub(/\/32$/, "", host_target)
      }
      $1 == target || (target ~ /\/32$/ && $1 == host_target) {
        found_gateway = ""
        found_device = ""
        for (i = 1; i < NF; i++) {
          if ($i == "via") found_gateway = $(i + 1)
          if ($i == "dev") found_device = $(i + 1)
        }
        if (found_gateway == gateway && found_device == device) found = 1
      }
      END { exit(found ? 0 : 1) }
    '
}

ensure_route_exemption() {
  _route_target="$1"
  _route_gateway="$2"
  _route_device="$3"
  _route_description="$4"

  if route_exemption_matches \
    "${_route_target}" "${_route_gateway}" "${_route_device}"; then
    return 0
  fi
  if ip -4 route replace "${_route_target}" \
    via "${_route_gateway}" dev "${_route_device}" 2>/dev/null; then
    echo "Route exemption updated: ${_route_description} via=${_route_gateway} dev=${_route_device}"
  else
    echo "Route exemption update failed: ${_route_description} via=${_route_gateway} dev=${_route_device}" >&2
  fi
}

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
        ensure_route_exemption \
          "${_ip}/32" "${_gw}" "${_dev}" "host=${_host} ip=${_ip}/32"
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
      ensure_route_exemption \
        "${_cidr}" "${_gw}" "${_dev}" "cidr=${_cidr}"
    done
    IFS="$OLDIFS"
  fi
}

apply_wireguard_mtu() {
  [ -n "${MYST_VPN_WIREGUARD_MTU:-}" ] || return 0

  if ! ip link show myst0 >/dev/null 2>&1; then
    return 0
  fi

  _current_mtu="$(ip -o link show myst0 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i == "mtu") {print $(i+1); exit}}')"
  if [ -z "${_current_mtu}" ]; then
    return 0
  fi

  if [ "${_current_mtu}" != "${MYST_VPN_WIREGUARD_MTU}" ]; then
    if ip link set dev myst0 mtu "${MYST_VPN_WIREGUARD_MTU}" >/dev/null 2>&1; then
      echo "WireGuard MTU: set myst0 mtu=${MYST_VPN_WIREGUARD_MTU} (was ${_current_mtu})"
    else
      echo "WireGuard MTU: failed to set myst0 mtu=${MYST_VPN_WIREGUARD_MTU} (current ${_current_mtu})"
    fi
  fi
}
