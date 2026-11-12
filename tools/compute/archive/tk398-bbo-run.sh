#!/bin/bash
# TK-398 п.2: отклик середины Gate фьюча (пересобранный BBO TK-394) на ход DEX, 4 слоя; затем суточная кластеризация. NX ядер (15).
set -e
cd /data/tk0380/tk392; NX=${NX:-15}; PY=/data/tk0380/npvenv/bin/python; E=/data/tk0380/tk398-event-bbo.py
mkdir -p cb1 cb3 cb4 cb5
run() { # слой, каталог mid, каталог выхода, список "монета [нога]"
  xargs -P $NX -L1 sh -c '[ -s '$3'/$0-curve.csv ] || '$PY' '$E' $0 '$2'/$0.csv.gz '$3'/$0 $1 > '$3'/$0.log 2>&1'
}
ls mid | sed 's/.csv.gz//' | run v1 mid cb1
run v3 mid3 cb3 < list3.txt
sed 's/ *$//' list4.txt | run v4 mid4 cb4
sed 's/ *$//' list5.txt | run v5 mid5 cb5
$PY tk398-days.py days398bbo.csv v3stable=cb1 v3quota=cb3 v2v4=cb4 sol=cb5 > days398bbo.log 2>&1
echo DONE > bbo398.done
