#!/bin/sh
set -eu

# Default mode preserves historical behavior.
if [ "$#" -eq 0 ]; then
  set -- serve
fi

# Thread additional args from environment only for serve mode.
if [ "$1" = "serve" ] && [ -n "${TEEP_SERVE_ARGS:-}" ]; then
  # Intentional word splitting so multiple flags can be passed via env.
  # shellcheck disable=SC2086
  exec /usr/local/bin/teep serve ${TEEP_SERVE_ARGS}
fi

exec /usr/local/bin/teep "$@"
