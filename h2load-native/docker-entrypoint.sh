#!/bin/sh

set -eu

NETEM_DELAY="${NETEM_DELAY:-50ms}"
NETEM_LOSS="${NETEM_LOSS:-2%}"

if ! command -v tc >/dev/null 2>&1; then
    echo "Network simulation requires tc (iproute2) in the container image." >&2
    exit 1
fi
if [ -n "${NETEM_RATE:-}" ]; then
    tc qdisc replace dev eth0 root netem delay "${NETEM_DELAY}" loss "${NETEM_LOSS}" rate "${NETEM_RATE}"
else
    tc qdisc replace dev eth0 root netem delay "${NETEM_DELAY}" loss "${NETEM_LOSS}"
fi

exec "$@"
