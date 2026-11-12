#!/usr/bin/env bash
# TK-407 добавка: AGT (173 ч gev2) строился одним процессом и был оборван — достроить по часам (-P15), пересчитать ev только AGT.
#   python3 /data/sched/alsched.py submit --cls prod --name tk407-agt --cores 15 --mem 20 --max-runtime 1h --est 15m --cwd /data/tk0393/tk407 -- bash /data/tk0393/tk407/agt.sh
B=/data/tk0393/tk407; K=/data/tk0380/tk392; PY=/data/tk0380/npvenv/bin/python; G=/data/tk0393/tk401/gev2all
OB=/srv/alpha-sda/tk379-ob/futures_usdt/orderbooks/202610; export GEV=$G; rm -f $B/agt.done
mkdir -p $B/gev2/AGT; ln -sfn $B/gev2/AGT $G/AGT
for f in $OB/AGT_USDT-20261015*.csv.gz $OB/AGT_USDT-2026101[6-9]*.csv.gz $OB/AGT_USDT-2026102[0-2]*.csv.gz; do
  h=$(basename $f .csv.gz); h=${h##*-}; [ -f $B/gev2/AGT/$h.npy ] || echo "AGT $f"; done \
 | xargs -P15 -L1 sh -c '$0 /data/tk0402/tk402-gate-bbo-ev.py '$B'/gev2 "$1" "$2" >/dev/null 2>&1' $PY
n=$(ls $B/gev2/AGT/*.npy | grep -vc tmp); echo "AGT npy $n"
m=$(for x in mid mid3 mid4 mid5; do [ -f $K/$x/AGT.csv.gz ] && echo $x; done | head -1)
leg=$(awk '$1=="AGT"{print $2}' $B/legs.txt)
rm -f $B/ev/AGT-* $B/ev/AGT.*
$PY /data/tk0393/tk407-ev.py AGT $K/$m/AGT.csv.gz $B/ev/AGT $leg > $B/ev/AGT.log 2>&1; echo rc=$? >> $B/ev/AGT.log; touch $B/ev/AGT.done
echo "AGT npy $n $(date +%T)" > $B/agt.done
