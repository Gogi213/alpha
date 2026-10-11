#!/bin/bash
# TK-132: гейт «байт в байт» бинарника alpha-tk132p1 (ветка tk132-p1 без r2, p12 влита) на стенде d15 и d01 против эталона e-jan/b5.
# Запуск юнитом: systemd-run --unit tk132-gate bash /data/tk0132/gate.sh; отчёт /data/tk0132/gate.out, маркер /data/tk0132/gate.done
rm -f /data/tk0132/gate.done; : > /data/tk0132/gate.out
for m in d15 d01; do
  /data/benchrun.sh stand bash /data/tk051/stand.sh alpha-tk132p1 $m TAG=tk132-$m >> /data/tk0132/gate.out 2>&1
  echo "rc $m $?" >> /data/tk0132/gate.out
  cat /data/tk051/stand-out/tk132-$m-$m-alpha-tk132p1/metrics.txt >> /data/tk0132/gate.out 2>&1
done
touch /data/tk0132/gate.done
