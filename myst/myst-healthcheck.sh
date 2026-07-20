#!/bin/sh
set -eu

ARMED_NAME="armed"
FAILURE_NAME="first-failure-uptime"
LOCK_NAME=".lock"

fail() {
  echo "Myst health supervisor failed: $*" >&2
  exit 1
}

validate_regular_file() {
  _path="$1"
  _description="$2"
  if [ -L "${_path}" ] || { [ -e "${_path}" ] && [ ! -f "${_path}" ]; }; then
    fail "${_description} has an unsafe file type"
  fi
}

prepare_state_dir() {
  if [ -L "${state_dir}" ] || { [ -e "${state_dir}" ] && [ ! -d "${state_dir}" ]; }; then
    fail "state directory has an unsafe file type"
  fi
  if [ ! -d "${state_dir}" ]; then
    mkdir -m 700 "${state_dir}" || fail "could not create state directory"
  fi
  chmod 700 "${state_dir}" || fail "could not secure state directory"
}

release_lock() {
  trap - EXIT HUP INT TERM
  if [ -n "${lock_dir:-}" ] && [ -d "${lock_dir}" ] && [ ! -L "${lock_dir}" ]; then
    rm -f "${lock_dir}/owner"
    rmdir "${lock_dir}" 2>/dev/null || true
  fi
}

acquire_lock() {
  lock_dir="${state_dir}/${LOCK_NAME}"
  _attempt=0
  while ! mkdir -m 700 "${lock_dir}" 2>/dev/null; do
    if [ -L "${lock_dir}" ] || [ ! -d "${lock_dir}" ]; then
      fail "state lock has an unsafe file type"
    fi

    _owner_file="${lock_dir}/owner"
    if [ -L "${_owner_file}" ] || { [ -e "${_owner_file}" ] && [ ! -f "${_owner_file}" ]; }; then
      fail "state lock owner has an unsafe file type"
    fi

    _owner=""
    if [ -f "${_owner_file}" ]; then
      IFS= read -r _owner < "${_owner_file}" || true
    fi
    case "${_owner}" in
      ''|*[!0-9]*) _owner_live=true ;;
      *)
        if kill -0 "${_owner}" 2>/dev/null; then
          _owner_live=true
        else
          _owner_live=false
        fi
        ;;
    esac

    # An empty owner is possible only in the tiny interval between mkdir and
    # the owner write. After one second it is safe to reclaim as a crashed
    # acquisition. A recorded dead owner is reclaimable immediately.
    if [ "${_owner_live}" = "false" ] || { [ -z "${_owner}" ] && [ "${_attempt}" -ge 20 ]; }; then
      rm -f "${_owner_file}"
      rmdir "${lock_dir}" 2>/dev/null || true
      _attempt=0
      continue
    fi

    _attempt="$(( _attempt + 1 ))"
    [ "${_attempt}" -lt 180 ] || fail "timed out waiting for state lock"
    sleep 0.05
  done

  printf '%s\n' "$$" > "${lock_dir}/owner" || {
    rmdir "${lock_dir}" 2>/dev/null || true
    fail "could not record state lock owner"
  }
  trap release_lock EXIT HUP INT TERM
}

atomic_write() {
  _path="$1"
  _value="$2"
  _temporary="$(mktemp "${_path}.tmp.XXXXXX")" || fail "could not create state file"
  if ! printf '%s\n' "${_value}" > "${_temporary}"; then
    rm -f "${_temporary}"
    fail "could not write state file"
  fi
  chmod 600 "${_temporary}" || {
    rm -f "${_temporary}"
    fail "could not secure state file"
  }
  mv "${_temporary}" "${_path}" || {
    rm -f "${_temporary}"
    fail "could not publish state file"
  }
}

read_uptime() {
  validate_regular_file "${uptime_file}" "monotonic uptime source"
  [ -r "${uptime_file}" ] || fail "monotonic uptime source is unreadable"
  _uptime=""
  IFS=' ' read -r _uptime _rest < "${uptime_file}" || true
  _uptime="${_uptime%%.*}"
  case "${_uptime}" in
    ''|*[!0-9]*) fail "monotonic uptime source is malformed" ;;
  esac
  printf '%s\n' "${_uptime}"
}

reset_state() {
  state_dir="$1"
  prepare_state_dir
  acquire_lock
  armed_file="${state_dir}/${ARMED_NAME}"
  failure_file="${state_dir}/${FAILURE_NAME}"
  validate_regular_file "${armed_file}" "armed state"
  validate_regular_file "${failure_file}" "failure timestamp"
  rm -f "${armed_file}" "${failure_file}" || fail "could not reset health state"
}

check_state() {
  target_pid="$1"
  state_dir="$2"
  readiness_script="$3"
  uptime_file="$4"
  grace_seconds="$5"

  case "${target_pid}" in
    ''|*[!0-9]*) fail "target PID must be a positive integer" ;;
  esac
  [ "${target_pid}" -gt 0 ] || fail "target PID must be a positive integer"
  case "${grace_seconds}" in
    ''|*[!0-9]*) fail "grace period must be an integer" ;;
  esac
  validate_regular_file "${readiness_script}" "readiness script"
  [ -r "${readiness_script}" ] || fail "readiness script is unreadable"

  # Explicit no-VPN and invalid selector values never create or mutate VPN
  # recovery state. The pure readiness predicate remains authoritative.
  case "${MYST_VPN_ENABLED:-true}" in
    true) ;;
    false) exec /bin/sh "${readiness_script}" ;;
    *) exec /bin/sh "${readiness_script}" ;;
  esac

  set +e
  /bin/sh "${readiness_script}"
  readiness_status="$?"
  set -e

  prepare_state_dir
  acquire_lock
  armed_file="${state_dir}/${ARMED_NAME}"
  failure_file="${state_dir}/${FAILURE_NAME}"
  validate_regular_file "${armed_file}" "armed state"
  validate_regular_file "${failure_file}" "failure timestamp"

  armed_value=""
  if [ -f "${armed_file}" ]; then
    IFS= read -r armed_value < "${armed_file}" || true
    case "${armed_value}" in
      armed|signaled) ;;
      *) fail "armed state is malformed" ;;
    esac
  fi

  if [ "${readiness_status}" -eq 0 ]; then
    # Once termination has been requested, keep health false until PID 1 exits
    # instead of rearming during daemon shutdown.
    [ "${armed_value}" != "signaled" ] || exit 1
    if [ -z "${armed_value}" ]; then
      atomic_write "${armed_file}" "armed"
    fi
    if [ -f "${failure_file}" ]; then
      rm -f "${failure_file}" || fail "could not clear failure timestamp"
    fi
    exit 0
  fi

  if [ "${armed_value}" != "armed" ]; then
    # Missing state is deliberately unarmed. Clear only a safe stale timestamp.
    rm -f "${failure_file}" || fail "could not clear unarmed failure timestamp"
    exit "${readiness_status}"
  fi

  now="$(read_uptime)"
  if [ ! -f "${failure_file}" ]; then
    atomic_write "${failure_file}" "${now}"
    exit "${readiness_status}"
  fi

  first_failure=""
  IFS= read -r first_failure < "${failure_file}" || true
  case "${first_failure}" in
    ''|*[!0-9]*)
      echo "Myst health supervisor warning: malformed failure timestamp was reset" >&2
      atomic_write "${failure_file}" "${now}"
      exit "${readiness_status}"
      ;;
  esac
  if [ "${now}" -lt "${first_failure}" ]; then
    echo "Myst health supervisor warning: invalid failure timestamp was reset" >&2
    atomic_write "${failure_file}" "${now}"
    exit "${readiness_status}"
  fi
  [ "$(( now - first_failure ))" -ge "${grace_seconds}" ] || exit "${readiness_status}"

  atomic_write "${armed_file}" "signaled"
  echo "Myst readiness failed continuously for ${grace_seconds}s; requesting graceful container restart" >&2
  if ! kill -TERM "${target_pid}"; then
    echo "Myst health supervisor failed: could not signal target PID" >&2
  fi
  exit "${readiness_status}"
}

umask 077
action="${1:-}"
case "${action}" in
  reset)
    [ "$#" -eq 2 ] || fail "usage: reset STATE_DIR"
    reset_state "$2"
    ;;
  check)
    [ "$#" -eq 6 ] || fail "usage: check TARGET_PID STATE_DIR READINESS_SCRIPT UPTIME_FILE GRACE_SECONDS"
    check_state "$2" "$3" "$4" "$5" "$6"
    ;;
  *) fail "expected reset or check action" ;;
esac
