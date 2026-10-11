#!/bin/bash
# TK-048: A/B декода v3 (t_decode_s пробы): OLD против NEW, 3 пары вперемешку на RIVER 01-02, 1 поток; выход /data/tk048/abdec/res.txt, маркер done.
OLD=${OLD:-/opt/alpha-compute/bin/alpha-b24probe}; NEW=${NEW:-/opt/alpha-compute/bin/alpha-tk048uv}
O=/data/tk048/abdec; mkdir -p $O; rm -f $O/done $O/res.txt
f=/data/tk046/jan/home/alpha/epochs/e-jan/study/root-2026-01-02/RIVERUSDT-2026-01-02.binlog
export ALPHA_DIRECT_FEED=1
for i in 1 2 3; do for v in old new; do
  b=$OLD; [ $v = new ] && b=$NEW
  $b lob event-cache-probe --binlog "$f" --out-json $O/$v$i.json > $O/$v$i.txt 2>&1
  echo "$v$i $(tr ' ' '\n' < $O/$v$i.txt | grep -E 't_decode_s|n_events|ok' | tr '\n' ' ')" >> $O/res.txt
done; done
touch $O/done
