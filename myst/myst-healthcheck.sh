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

load_process_start() {
  _process_pid="$1"
  case "${_process_pid}" in
    ''|*[!0-9]*) fail "process identity PID is malformed" ;;
  esac
  _stat_file="${process_root}/${_process_pid}/stat"
  validate_regular_file "${_stat_file}" "process identity source"
  if [ ! -e "${_stat_file}" ]; then
    return 1
  fi
  [ -r "${_stat_file}" ] || fail "process identity source is unreadable"
  _stat_line=""
  IFS= read -r _stat_line < "${_stat_file}" || true
  case "${_stat_line}" in
    *') '*) _stat_fields="${_stat_line##*) }" ;;
    *) fail "process identity source is malformed" ;;
  esac
  set -f
  set -- ${_stat_fields}
  set +f
  [ "$#" -ge 20 ] || fail "process identity source is malformed"
  shift 19
  process_start="$1"
  case "${process_start}" in
    ''|*[!0-9]*) fail "process identity source is malformed" ;;
  esac
}

release_lock() {
  trap - EXIT HUP INT TERM
  if [ "${lock_acquired:-false}" = "true" ] \
    && [ -n "${lock_dir:-}" ] \
    && [ -d "${lock_dir}" ] \
    && [ ! -L "${lock_dir}" ]; then
    _release_owner=""
    if [ -f "${lock_dir}/owner" ] && [ ! -L "${lock_dir}/owner" ]; then
      IFS= read -r _release_owner < "${lock_dir}/owner" || true
    fi
    if [ "${_release_owner}" = "${lock_owner:-}" ]; then
      rm -f "${lock_dir}/owner"
      rmdir "${lock_dir}" 2>/dev/null || true
    fi
  fi
  if [ -n "${claim_file:-}" ] && [ -f "${claim_file}" ] && [ ! -L "${claim_file}" ]; then
    rm -f "${claim_file}"
  fi
}

acquire_lock() {
  lock_dir="${state_dir}/${LOCK_NAME}"
  lock_acquired=false
  claim_file="${state_dir}/.lock-owner.$$"
  validate_regular_file "${claim_file}" "state lock claim"
  printf '%s\n' "${lock_owner}" > "${claim_file}" || fail "could not create state lock claim"
  chmod 600 "${claim_file}" || fail "could not secure state lock claim"
  trap release_lock EXIT HUP INT TERM
  _attempt=0
  while true; do
    if mkdir -m 700 "${lock_dir}" 2>/dev/null; then
      # The hard-link creation is the atomic ownership decision. If an empty
      # abandoned directory was reclaimed and recreated while a former creator
      # was paused, only one claimant can publish the fixed owner name.
      if ln "${claim_file}" "${lock_dir}/owner" 2>/dev/null; then
        lock_acquired=true
        return 0
      fi
      rmdir "${lock_dir}" 2>/dev/null || true
    fi

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
    if [ -z "${_owner}" ]; then
      _owner_live=true
    else
      case "${_owner}" in
        *:*)
          _owner_pid="${_owner%%:*}"
          _owner_start="${_owner#*:}"
          case "${_owner_pid}" in
            ''|*[!0-9]*) fail "state lock owner is malformed" ;;
          esac
          case "${_owner_start}" in
            ''|*[!0-9]*) fail "state lock owner is malformed" ;;
          esac
          if kill -0 "${_owner_pid}" 2>/dev/null \
            && load_process_start "${_owner_pid}" \
            && [ "${process_start}" = "${_owner_start}" ]; then
            _owner_live=true
          else
            _owner_live=false
          fi
          ;;
        *) fail "state lock owner is malformed" ;;
      esac
    fi

    # An empty owner is possible only in the tiny interval between mkdir and
    # the owner write. After one second it is safe to reclaim as a crashed
    # acquisition. A recorded dead owner is reclaimable immediately.
    if [ "${_owner_live}" = "false" ] || { [ -z "${_owner}" ] && [ "${_attempt}" -ge 20 ]; }; then
      if [ "${_owner_live}" = "false" ]; then
        rm -f "${_owner_file}"
      fi
      rmdir "${lock_dir}" 2>/dev/null || true
      _attempt=0
      continue
    fi

    _attempt="$(( _attempt + 1 ))"
    [ "${_attempt}" -lt 180 ] || fail "timed out waiting for state lock"
    sleep 0.05
  done
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
  process_root="$2"
  prepare_state_dir
  if [ -L "${process_root}" ] || [ ! -d "${process_root}" ]; then
    fail "process identity root has an unsafe file type"
  fi
  load_process_start "$$" || fail "current process identity is unavailable"
  lock_owner="$$:${process_start}"
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
  process_root="$6"

  case "${target_pid}" in
    ''|*[!0-9]*) fail "target PID must be a positive integer" ;;
  esac
  [ "${target_pid}" -gt 0 ] || fail "target PID must be a positive integer"
  case "${grace_seconds}" in
    ''|*[!0-9]*) fail "grace period must be an integer" ;;
  esac
  validate_regular_file "${readiness_script}" "readiness script"
  [ -r "${readiness_script}" ] || fail "readiness script is unreadable"

  # Standalone signup/payment mode may remain active for hours or days. Its
  # local readiness remains visible, but a failure must never terminate PID 1
  # or turn an upstream signup/order failure into container restart behavior.
  case "${MYST_SETUP_ONLY:-false}" in
    true) exec /bin/sh "${readiness_script}" ;;
    false) ;;
    *) exec /bin/sh "${readiness_script}" ;;
  esac

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
  if [ -L "${process_root}" ] || [ ! -d "${process_root}" ]; then
    fail "process identity root has an unsafe file type"
  fi
  load_process_start "$$" || fail "current process identity is unavailable"
  lock_owner="$$:${process_start}"
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
    atomic_write "${armed_file}" "armed"
  fi
  exit "${readiness_status}"
}

umask 077
action="${1:-}"
case "${action}" in
  reset)
    [ "$#" -eq 3 ] || fail "usage: reset STATE_DIR PROCESS_ROOT"
    reset_state "$2" "$3"
    ;;
  check)
    [ "$#" -eq 7 ] || fail "usage: check TARGET_PID STATE_DIR READINESS_SCRIPT UPTIME_FILE GRACE_SECONDS PROCESS_ROOT"
    check_state "$2" "$3" "$4" "$5" "$6" "$7"
    ;;
  *) fail "expected reset or check action" ;;
esac
