#!/bin/bash
# TK-490 (В-94): закачка свечей 1 м и фандинга пампов на Steam Deck, всё в ~/alpha-pump/ (чужое не трогаем).
# usage: tk490-run.sh universe | candles | funding | check | status     (каждый шаг — юнит systemd --user, готовое пропускается)
# Период: 2025-01-01 00:00 UTC … 2026-11-03 23:59 UTC. Вселенная — universe/symbols-<биржа>.txt (tk490_universe.py).
set -eu
H=$HOME/alpha-pump; B=$H/bin; U=$H/universe; L=$H/log
START_MS=1735689600000                              # 2025-01-01 00:00 UTC
END_MS=$(( $(date -u -d 2026-11-04 +%s) * 1000 ))   # 2026-11-04 00:00 UTC: последние полные сутки — 03.11
run() { local n=$1; shift; systemd-run --user --unit="$n" --working-directory="$H" -p StandardOutput=file:"$L/$n.log" -p StandardError=file:"$L/$n.log" "$@"; }
mkdir -p "$L" "$H/candles/bybit-linear" "$H/candles/cov" "$H/candles/binance-um" "$H/funding/bybit-linear" "$H/funding/binance-um" "$H/check"
case "$1" in
universe) run pump-universe python3 "$B/tk490_universe.py" "$U" --last-day 2026-11-03 ;;
candles)
  [ -f "$U/universe.done" ] || { echo "нет universe.done"; exit 2; }
  run pump-candles-bybit env K_GZIP=1 K_START=$START_MS K_END=$END_MS python3 "$B/tk476-bybit-hist.py" "$H/candles" "$U/symbols-bybit-linear.txt" 64 80
  run pump-candles-binance env TK450_ROOT="$H/candles/binance-um" TK450_PY="$B/tk450-binance-klines.py" TK450_FIRST=2025-01 TK476_THREADS=8 python3 "$B/tk476-binance-new.py" "$U/symbols-binance-um.txt" ;;
funding)
  [ -f "$U/universe.done" ] || { echo "нет universe.done"; exit 2; }
  for ex in bybit binance; do
    x=$([ $ex = bybit ] && echo bybit-linear || echo binance-um)
    run pump-funding-$ex env TK478_FROM_MS=$START_MS TK478_SYMBOLS="$U/symbols-$x.txt" python3 "$B/tk478-funding.py" $ex "$H/funding" "$U" 8
  done ;;
check)
  for x in bybit-linear binance-um; do
    python3 "$B/tk490_check.py" candles "$H/candles/$x" "$U/symbols-$x.txt" "$H/check/candles-$x.csv" || true
    python3 "$B/tk490_check.py" funding "$H/funding/$x" "$U/symbols-$x.txt" "$H/check/funding-$x.csv" || true
  done ;;
status) systemctl --user list-units 'pump-*' --all --no-legend; du -sh "$H/candles" "$H/funding" 2>/dev/null; tail -n1 "$L"/*.log ;;
esac
