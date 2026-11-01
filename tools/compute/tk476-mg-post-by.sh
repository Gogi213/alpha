#!/usr/bin/env bash
# TK-476: после delta.done (+ в стадии нет .part, во входящих пусто) — слияние Bybit-дельты. Прежнюю таблицу сверки сохраняет.
cd /data/tk0476 || exit 1
S=/srv/alpha-sda/candles-1m-476
rm -f mg-by2.done mg-by2.fail
while [ ! -e delta.done ]; do
  [ -e delta.fail ] && { echo "fail $(date -u +%F\ %T) UTC: delta.fail" > mg-by2.fail; exit 1; }
  sleep 30
done
while [ -n "$(ls $S/bybit-linear | grep -E 'part|tmp')" ] || [ -n "$(ls incoming/cov 2>/dev/null)" ]; do sleep 20; done
[ -e "$S/overlap-bybit-linear.csv" ] && cp -n "$S/overlap-bybit-linear.csv" "$S/overlap-bybit-linear-1.csv"
exec bash /data/tk0476/tk476-mg-run.sh bybit-linear 6 mg-by2
