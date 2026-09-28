#!/usr/bin/env bash
# TK-018: ждать конца всех заданий p07-jall (и j9 июля), затем ворота 03.08 -> маркер
cd /home/deck/alpha
GATE_DONE=""
while :; do
  n=$(ls queue/pending queue/running 2>/dev/null | grep -cE -- '-p07-(jall|j9)-')
  if [ -z "$GATE_DONE" ] && ! ls queue/pending queue/running 2>/dev/null | grep -q -- '^4-.*-p07-jall-'; then
    python3 bin/p07-all-jul.py --gate > tmp-p07/jall-gate.txt 2>&1; GATE_DONE=1
    tail -1 tmp-p07/jall-gate.txt > tmp-p07/jall-gate.status
  fi
  [ "$n" = 0 ] && break
  sleep 120
done
ls queue/failed | grep -E -- '-p07-jall-' > tmp-p07/jall-failed.txt || true
date -u +%FT%TZ > tmp-p07/jall-cells.finished
