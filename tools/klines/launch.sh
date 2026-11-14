#!/usr/bin/env bash
# Run on srv: launch.sh <unit-suffix> <klines.py args...>   e.g. launch.sh prep symbols --root /data/klines
set -euo pipefail
UNIT="${1:?unit suffix}"; shift
mkdir -p /data/klines
exec systemd-run --unit="klines-$UNIT" --collect --nice=19 \
  -p IOSchedulingClass=idle -p CPUQuota=200% -p MemoryMax=6G \
  -p StandardOutput=append:/data/klines/run.log -p StandardError=append:/data/klines/run.log \
  python3 -I /data/klines/bin/klines.py "$@"
