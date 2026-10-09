#!/usr/bin/env bash
# TK-018: ворота 03.08 после задания prio 4 p07-jall; конец — когда у ВСЕХ 31 суток июля (дека + VPS) есть
# .done последнего прохода (Г-86) и п05-c; ни одного p07-jall в очереди.
cd /home/deck/alpha
H=epochs/e-jul/b5
GATE_DONE=""
while :; do
  q=$(ls queue/pending queue/running 2>/dev/null)
  if [ -z "$GATE_DONE" ] && [ -e epochs/e-aug/b5/p02-h9e899-market-julgate/2026-08-03/.done ] \
     && ! echo "$q" | grep -q -- '^4-.*-p07-jall-'; then
    python3 bin/p07-all-jul.py --gate > tmp-p07/jall-gate.txt 2>&1; GATE_DONE=1
    tail -1 tmp-p07/jall-gate.txt > tmp-p07/jall-gate.status
  fi
  miss=0
  for d in $(seq -w 1 31); do
    [ -e $H/p02-h9e899-market/2026-07-$d/.done ] && [ -e $H/p05-c/2026-07-$d/.done ] || miss=$((miss+1))
  done
  echo "$(date -u +%FT%TZ) нет суток: $miss" > tmp-p07/jall-progress.txt
  [ "$miss" = 0 ] && [ -n "$GATE_DONE" ] && ! echo "$q" | grep -q -- '-p07-jall-' && break
  sleep 120
done
ls queue/failed | grep -E -- '-p07-jall-' > tmp-p07/jall-failed.txt || true
date -u +%FT%TZ > tmp-p07/jall-cells.finished
