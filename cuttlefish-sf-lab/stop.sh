#!/usr/bin/env bash
set -Eeuo pipefail
LAB_ROOT="${CF_LAB_ROOT:-$HOME/cuttlefish-sf-q1}"
INSTANCE="$LAB_ROOT/instance"
cd "$INSTANCE"
HOME="$INSTANCE" ./bin/stop_cvd
