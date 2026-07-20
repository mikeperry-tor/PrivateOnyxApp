#!/bin/sh

# The caller owns these exact child PIDs. Keep signal forwarding and reaping in
# one sourceable helper so the production entrypoint and deterministic harness
# exercise the same shutdown implementation.
svc_pid=""
route_fix_pid=""

stop_children() {
  trap - INT TERM
  if [ -n "${route_fix_pid}" ]; then
    kill "${route_fix_pid}" 2>/dev/null || true
  fi
  if [ -n "${svc_pid}" ]; then
    kill "${svc_pid}" 2>/dev/null || true
  fi
  if [ -n "${route_fix_pid}" ]; then
    wait "${route_fix_pid}" 2>/dev/null || true
  fi
  if [ -n "${svc_pid}" ]; then
    wait "${svc_pid}" 2>/dev/null || true
  fi
}

handle_shutdown() {
  stop_children
  exit 0
}

install_shutdown_handler() {
  trap handle_shutdown INT TERM
}
