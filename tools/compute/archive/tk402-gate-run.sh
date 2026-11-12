#!/usr/bin/env bash
# TK-402 шаг 2: событийный BBO Gate фьюча (tk402-gate-bbo-ev.py) по 459 общим монетам (stage1.tsv, bybit_fut не пуст), 15–22.10, 15 процессов, крупные первыми.
#   python3 /data/sched/alsched.py submit --cls prod --kind work --name tk402-gate --cores 15 --mem 24 --disk hdd1 --max-runtime 3h --cwd /data/tk0402 -- bash /data/tk0402/tk402-gate-run.sh
# Выход /data/tk0402/gev2/<COIN>/<YYYYMMDDHH>.npy, метка /data/tk0402/gate2.done (или gate2.failed); охват gate-cover.tsv; v2: bq/aq, событие = смена цены или размера. Посекундных сеток нет.
set -uo pipefail
W=/data/tk0402; OB=/srv/alpha-sda/tk379-ob/futures_usdt/orderbooks/202610; PY=/data/tk0380/npvenv/bin/python
cd $W; rm -rf gev2/TEST gate2.done gate2.failed; mkdir -p gev2
awk -F'\t' 'NR>1 && $2!=""{print $1}' stage1.tsv | while read c; do
  fs=$(ls $OB/${c}_USDT-20261015*.csv.gz $OB/${c}_USDT-2026101[6-9]*.csv.gz $OB/${c}_USDT-2026102[0-2]*.csv.gz 2>/dev/null | tr '\n' ' ')
  [ -n "$fs" ] || { echo "$c нет книги Gate" >> gate-missing.txt; continue; }
  echo "$(du -cb $fs | tail -1 | cut -f1) $c"
done | sort -rn | cut -d' ' -f2 > gate-units.txt
echo "монет $(wc -l < gate-units.txt) старт $(date +%T)" > gate2.log
xargs -a gate-units.txt -P15 -n1 sh -c '$0 /data/tk0402/tk402-gate-bbo-ev.py /data/tk0402/gev2 "$1" >/dev/null' $PY; rc=$?
echo "rc=$rc конец $(date +%T); монет с npy $(ls gev2 | wc -l)" >> gate2.log
# охват (B14): каждая недостающая монета-час — с причиной; done только если неучтённых нет
$PY /data/tk0402/tk402-gate-cover.py /data/tk0402/gev2 /data/tk0402/gate-cover.tsv && [ "$rc" = 0 ] && echo '{"done":1,"cover":"gate-cover.tsv"}' > gate2.done || touch gate2.failed
