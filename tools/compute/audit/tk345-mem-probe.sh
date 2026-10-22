#!/usr/bin/env bash
# TK-345: замер пика RSS задачи прохода валидности — один процесс, выборка VmHWM раз в 10 с, потолок времени T (с).
#   tk345-mem-probe.sh <метка> <сутки> <GRP> <символы через пробел>       env: T (600) FAMS (F1)
set -uo pipefail
lab=$1; d=$2; export GRP=$3; export PROBE_SYMS=$4; T=${T:-600}; export FAMS=${FAMS:-F1}
O=/data/tk0345/probe-$lab; rm -rf "$O"; mkdir -p "$O"
timeout "$T" bash /data/tk0345/tk345-validity-pass.sh "$d" 0 "$O/run" &
bp=$!
while kill -0 $bp 2>/dev/null; do
  m=$(for p in $(pgrep -f "alpha-tk0289c-pgo.*$O/run"); do awk '/VmHWM/{print $2}' /proc/$p/status 2>/dev/null; done | sort -n | tail -1)
  echo "$(date +%s) hwm_kb=${m:-0}" >> "$O/hwm.txt"; sleep 10
done
pkill -f "alpha-tk0289c-pgo.*$O/run" || true
wait $bp; echo "rc=$? max_hwm_kb=$(awk -F= '{print $2}' "$O/hwm.txt" | sort -n | tail -1)" > "$O/result.txt"
