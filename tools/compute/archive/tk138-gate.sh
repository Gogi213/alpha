#!/bin/bash
# TK-138: гейт «байт в байт» бинарника alpha-tk138p2b (ветка tk138-p1 без r2, p12 влита) на стенде d15 и d01 против эталона e-jan/b5.
# Запуск юнитом: systemd-run --unit tk138-gate bash /data/tk0138/gate.sh; отчёт /data/tk0138/gate.out, маркер /data/tk0138/gate.done
rm -f /data/tk0138/gate.done; : > /data/tk0138/gate.out
for m in d15 d01; do
  /data/benchrun.sh stand bash /data/tk051/stand.sh alpha-tk138p2b $m TAG=tk138-$m >> /data/tk0138/gate.out 2>&1
  echo "rc $m $?" >> /data/tk0138/gate.out
  cat /data/tk051/stand-out/tk138-$m-$m-alpha-tk138p2b/metrics.txt >> /data/tk0138/gate.out 2>&1
done
touch /data/tk0138/gate.done
