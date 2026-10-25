#!/usr/bin/env bash
# TK-402 шаг 2: событийный BBO Gate фьюча (tk402-gate-bbo-ev.py) по 459 общим монетам (stage1.tsv, bybit_fut не пуст), 15–22.10, 15 процессов, крупные первыми.
#   python3 /data/sched/alsched.py submit --cls prod --kind work --name tk402-gate --cores 15 --mem 24 --disk hdd1 --max-runtime 3h --cwd /data/tk0402 -- bash /data/tk0402/tk402-gate-run.sh
# Выход /data/tk0402/gev/<COIN>/<YYYYMMDDHH>.npy, метка /data/tk0402/gate.done (или gate.failed). Посекундных сеток нет.
set -uo pipefail
W=/data/tk0402; OB=/srv/alpha-sda/tk379-ob/futures_usdt/orderbooks/202610; PY=/data/tk0380/npvenv/bin/python
cd $W; rm -rf gev/TEST gate.done gate.failed; mkdir -p gev
awk -F'\t' 'NR>1 && $2!=""{print $1}' stage1.tsv | while read c; do
  fs=$(ls $OB/${c}_USDT-20261015*.csv.gz $OB/${c}_USDT-2026101[6-9]*.csv.gz $OB/${c}_USDT-2026102[0-2]*.csv.gz 2>/dev/null | tr '\n' ' ')
  [ -n "$fs" ] || { echo "$c нет книги Gate" >> gate-missing.txt; continue; }
  echo "$(du -cb $fs | tail -1 | cut -f1) $c $fs"
done | sort -rn | cut -d' ' -f2- > gate-units.txt
echo "монет $(wc -l < gate-units.txt) старт $(date +%T)" > gate.log
xargs -a gate-units.txt -P15 -L1 sh -c '$0 /data/tk0402/tk402-gate-bbo-ev.py /data/tk0402/gev "$@" >/dev/null' $PY; rc=$?
echo "rc=$rc конец $(date +%T); монет с npy $(ls gev | wc -l)" >> gate.log
if [ "$rc" = 0 ]; then echo '{"done":1}' > gate.done; else touch gate.failed; fi
