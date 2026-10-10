#!/bin/bash
# TK-084 гейт: бинарник с дедлайнами 6/8 ч на сутках марта (R2-скрипт суток) против готового боевого счёта /data/tk065/w-<сутки>, diff -rq по b5.
# Запуск: systemd-run --unit tk084-gate --collect /data/benchrun.sh stand bash /data/tk084/gate.sh [бинарник] [сутки]; выход /data/tk084/gate.txt, маркер gate.done
bin=${1:-alpha-tk084-dl}; d=${2:-2026-03-02}; REF=/data/tk065/w-$d; OUT=/data/tk084/gate.txt
mkdir -p /data/tk084; rm -f /data/tk084/gate.done $OUT
SCR=r2 bash /data/tk065/probe.sh g084-$d $bin mar $d
n=0; bad=0
for f in $(cd /data/tk065/g084-$d/b5 && find . -type f | sort); do n=$((n+1)); cmp -s /data/tk065/g084-$d/b5/$f $REF/b5/$f || { bad=$((bad+1)); echo "DIFF $f" >> $OUT; }; done
echo "gate $bin $d files $n diff $bad" >> $OUT; cat /data/tk065/g084-$d/t.txt >> $OUT; touch /data/tk084/gate.done
