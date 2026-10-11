#!/bin/bash
# TK-137: гейт «байт в байт» бинарника alpha-tk137p2a (ветка tk137-p2a без r2) на стенде d15 и d01 против эталона e-jan/b5.
# Запуск юнитом: systemd-run --unit tk137-gate bash /data/tk0137/gate.sh; отчёт /data/tk0137/gate.out, маркер /data/tk0137/gate.done
rm -f /data/tk0137/gate.done; : > /data/tk0137/gate.out
for m in d15 d01; do
  /data/benchrun.sh stand bash /data/tk051/stand.sh alpha-tk137p2a $m TAG=tk137-$m >> /data/tk0137/gate.out 2>&1
  echo "rc $m $?" >> /data/tk0137/gate.out
  cat /data/tk051/stand-out/tk137-$m-$m-alpha-tk137p2a/metrics.txt >> /data/tk0137/gate.out 2>&1
done
touch /data/tk0137/gate.done
