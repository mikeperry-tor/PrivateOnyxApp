#!/bin/sh
set -eu
exec python3 "$(dirname "$0")/vpn_cli.py" "$@"
