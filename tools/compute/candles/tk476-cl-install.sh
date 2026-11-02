#!/usr/bin/env bash
# TK-476: после cl.done (сборка 281 снятой монеты Bybit) — поставить свечи вместо header-only файлов основного каталога
# и пересчитать coverage по трём биржам (coverage2.csv, метка cov2.done). Без cl.done/с cl.fail — выходит, ничего не трогает.
# Прежний header-only файл — в $S/prev-closed (В-60). Непустой файл основного каталога — отказ по символу (не затирать).
cd /data/tk0476 || exit 1
S=/srv/alpha-sda
rm -f inst.done inst.fail cov2.done cov2.fail
while [ ! -e cl.done ]; do
  [ -e cl.fail ] && { echo "cl.fail $(date -u +%F\ %T) UTC" > inst.fail; exit 1; }
  sleep 30
done
mkdir -p $S/candles-1m-476/prev-closed
ok=0; bad=0
for s in $(cat tk476-closed-syms.txt); do
  src=$S/candles-1m-476/bybit-closed/$s.csv.gz; dst=$S/candles-1m/bybit-linear/$s.csv.gz
  if [ ! -e "$src" ] || ! gzip -t "$src" 2>/dev/null; then echo "BAD src $s"; bad=$((bad+1)); continue; fi
  if [ -e "$dst" ] && [ "$(zcat "$dst" | head -3 | wc -l)" -gt 1 ]; then echo "BAD dst не пуст $s"; bad=$((bad+1)); continue; fi
  [ -e "$dst" ] && cp -n "$dst" $S/candles-1m-476/prev-closed/$s.csv.gz
  cp "$src" "$dst.install.tmp" && mv "$dst.install.tmp" "$dst" && ok=$((ok+1))
done
echo "установлено $ok, отказов $bad"
[ "$bad" -eq 0 ] || { echo "отказов $bad" > inst.fail; exit 1; }
echo "ok $(date -u +%F\ %T)" > inst.done
/data/tk421/venv/bin/python tk476_coverage.py /data/tk0476/coverage2.csv 14 binance-um=$S/candles-1m/binance-um bybit-linear=$S/candles-1m/bybit-linear gate-futures_usdt=$S/candles-1m/gate-futures_usdt > coverage2.log 2>&1 \
  && echo "ok $(date -u +%F\ %T)" > cov2.done || echo "rc=$?" > cov2.fail
