#!/bin/sh
set -eu

# Build-time only, using the pinned Debian base's configured repositories.
export DEBIAN_FRONTEND=noninteractive
apt-get update --error-on=any
apt-get upgrade -y --no-install-recommends
rm -rf /var/lib/apt/lists/*
