#!/bin/bash
# TK-048 п.0а: lob event-cache-probe на сутках (A плоский 32 Б, B дельта-варинт, zstd 1/3). Запуск на сервере счёта.
# Выход /data/tk048/ecp/<SYM>-<день>.json + ecp.done; один поток, по одному дню подряд.
BIN=${BIN:-/opt/alpha-compute/bin/alpha-b24probe}
O=/data/tk048/ecp; mkdir -p $O; rm -f $O/ecp.done
for spec in "jan 2026-01-02 RIVERUSDT" "jan 2026-01-02 DOGEUSDT" "mar 2026-03-02 HYPEUSDT" "mar 2026-03-02 POWERUSDT" "mar 2026-03-02 ${LIGHT:-TRXUSDT}"; do
  set -- $spec
  f=/data/tk046/$1/home/alpha/epochs/e-$1/study/root-$2/$3-$2.binlog
  [ -e "$f" ] || { echo "нет $f" >> $O/ecp.err; continue; }
  $BIN lob event-cache-probe --binlog "$f" --out-json $O/$3-$2.json > $O/$3-$2.txt 2>&1
done
touch $O/ecp.done
