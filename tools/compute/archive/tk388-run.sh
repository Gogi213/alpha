#!/bin/bash
# TK-388 / P-27: BBO по часам -> реплика TK-386 (старый тариф, окно 15-22.10) -> OOS 01.09-14.10 (Q, LAPTOP, SUE; Solana позже). 15 ядер.
cd /data/tk0388; PY=/data/tk0380/npvenv/bin/python; X=15
OBR=/srv/alpha-sda/tk387-gate/futures_usdt/orderbooks
for c in Q LAPTOP SUE; do ls $OBR/202609/${c}_USDT-*.csv.gz $OBR/202610/${c}_USDT-*.csv.gz 2>/dev/null; done > bbo-files.txt
wc -l bbo-files.txt
xargs -a bbo-files.txt -n 12 -P $X $PY tk388-bbo-hour.py /data/tk0388/bbo
touch bbo.done; date
# реплика (старый тариф 5/2, окно 15-22.10, как TK-386): Q bsc, SUE bsc, LAPTOP base
mkdir -p rep; export MODE=med
$PY -u tk388-converge.py bsc Q rep/Q > rep/Q.log 2>&1 &
$PY -u tk388-converge.py bsc SUE rep/SUE > rep/SUE.log 2>&1 &
$PY -u tk388-converge.py base LAPTOP rep/LAPTOP > rep/LAPTOP.log 2>&1 &
wait; touch rep.done; date
$PY tk388-repcheck.py > rep-check.txt 2>&1
# боевой OOS: монета x T параллельно (12 процессов), тариф 7.5 / -1, контроль по ячейкам
export DSTART=2026-09-01 ND=44 DAY0=1 FEE_TK=7.5 FEE_MK=-1.0 BBOROOT=/data/tk0388/bbo SQDROOT=/srv/alpha-sda/tk387-dex SQYROOT=/srv/alpha-sda/tk387-dex/sync SEEDPER=1 FUNDCACHE=/data/tk0388/funding.json
mkdir -p oos
for T in 1.0 2.0 3.0 5.0; do
 TSEL=$T /usr/bin/time -v -o oos/Q-$T.time $PY -u tk388-converge.py bsc Q oos/Q > oos/Q-$T.log 2>&1 &
 TSEL=$T /usr/bin/time -v -o oos/SUE-$T.time $PY -u tk388-converge.py bsc SUE oos/SUE > oos/SUE-$T.log 2>&1 &
 TSEL=$T /usr/bin/time -v -o oos/LAPTOP-$T.time $PY -u tk388-converge.py base LAPTOP oos/LAPTOP > oos/LAPTOP-$T.log 2>&1 &
done
wait; touch oos.done; date
