#!/bin/sh

set -eu

NETEM_DELAY="${NETEM_DELAY:-50ms}"
NETEM_LOSS="${NETEM_LOSS:-2%}"

tc qdisc add dev eth0 root netem delay "${NETEM_DELAY}" loss "${NETEM_LOSS}" || true

exec ./https-server
