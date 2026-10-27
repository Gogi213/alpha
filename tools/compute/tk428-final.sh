#!/bin/bash
# TK-428 (G-017): после цепочек — охват по месяцам (tk411-finalize.py) и перенос свечей в /srv/alpha-sda/candles-1m-<ГГГГММ>/
# (те же имена каталогов, что у августа: bybit-linear, bybit-spot, gate-futures_usdt, gate-spot, coverage.csv).
# Использование: tk428-final.sh   (ROOT=/srv/alpha-sda/tk428). Ждёт конца Bybit-цепочки и сборки Gate 202606.
set -u
root=${ROOT:-/srv/alpha-sda/tk428}; here=$(dirname "$(readlink -f "$0")")
until grep -q '^bybit-oi rc=' "$root/log/run.log" && [ -e "$root/m/202606/build.done" ]; do sleep 60; done
# месяц  начало_мс  конец_мс
while read -r m a b; do
  r=$root/m/$m; d=/srv/alpha-sda/candles-1m-$m
  [ -d "$r/candles/fut" ] || mkdir -p "$r/candles/fut" "$r/candles/spot"   # июль: Gate берёт TK-425 (jul-gate), здесь только Bybit
  K_START=$a K_END=$b python3 -I "$here/tk411-finalize.py" "$r" "$r/log/miss.tsv" "$root/coverage-$m.csv" > "$root/log/finalize-$m.out" 2>&1
  echo "finalize-$m rc=$?" >> "$root/log/run.log"
  mkdir -p "$d"; mv "$r/candles/bybit-linear" "$d/bybit-linear"; mv "$r/candles/bybit-spot" "$d/bybit-spot"
  mv "$r/candles/fut" "$d/gate-futures_usdt"; mv "$r/candles/spot" "$d/gate-spot"; cp "$root/coverage-$m.csv" "$d/coverage.csv"
done <<'M'
202601 1767225600000 1769904000000
202602 1769904000000 1772323200000
202603 1772323200000 1775001600000
202604 1775001600000 1777593600000
202605 1777593600000 1780272000000
202606 1780272000000 1782864000000
202607 1782864000000 1784851200000
M
echo "final rc=0 $(date +%T)" >> "$root/log/run.log"; touch "$root/final.done"
