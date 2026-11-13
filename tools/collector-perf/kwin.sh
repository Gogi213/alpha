#!/bin/bash
# G-022 collector-perf K-window driver (на srv): kwin.sh <имя ручки> <команда применения> <команда отката> [секунд=180]
# Последовательность: база, применить, 20 с, окно ручки, откат (и при любом выходе), 20 с, база. Канарейка — ts_probe.py в каждом окне.
set -u
NAME=$1; APPLY=$2; REVERT=$3; SECS=${4:-180}
D=/data/perf/k/$NAME; mkdir -p "$D"
P=/data/perf
REVERTED=0
revert() {
  if [ $REVERTED = 0 ]; then
    REVERTED=1
    echo "$(date -u +%T) revert: $REVERT" >> "$D/log.txt"
    bash -c "$REVERT" >> "$D/log.txt" 2>&1
  fi
}
trap revert EXIT
win() { # метка
  python3 $P/kwin.py snap "$D/$1.a"
  timeout $((SECS + 30)) python3 $P/ts_probe.py $((SECS - 10)) 0 32 > "$D/$1.probe.txt" 2>&1 &
  sleep "$SECS"
  python3 $P/kwin.py snap "$D/$1.b"
  wait
  python3 $P/kwin.py report "$D/$1.a" "$D/$1.b" "$1" > "$D/$1.report.txt" 2>&1
  tail -3 "$D/$1.probe.txt" >> "$D/$1.report.txt"
  echo "$(date -u +%T) window $1 done" >> "$D/log.txt"
}
echo "$(date -u +%T) start $NAME" >> "$D/log.txt"
win base1
echo "$(date -u +%T) apply: $APPLY" >> "$D/log.txt"
bash -c "$APPLY" >> "$D/log.txt" 2>&1
sleep 20
win knob
revert
sleep 20
win base2
echo ALLDONE >> "$D/log.txt"
