#!/bin/sh

set -eu

NETEM_DELAY="${NETEM_DELAY:-50ms}"
NETEM_LOSS="${NETEM_LOSS:-2%}"

tc qdisc replace dev eth0 root netem delay "${NETEM_DELAY}" loss "${NETEM_LOSS}"

exec ./quic-https-server
