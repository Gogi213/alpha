#!/usr/bin/env bash
# TK-493 F6, G8b (шаг 2): три задания со снятым контрактом (Binance SETTLING, Bybit Closed, Aster SETTLING). Каждое — отдельным
# прогоном run (одно задание, фандинг включён, --mtm-open, --cells-out, --open-out), затем независимый пересчёт по сырью:
# все сделки, счётчики (entry_deferred, exit_deferred, open_at_end, gap_minutes), открытая позиция и её оценка по последнему close.
# Дополнительно: сумма суточного csv против суммы сделок (открытая позиция в P&L НЕ входит).
set -u
BIN=$HOME/xdl9/bin/statarb-xdl9-02c6a0df
P=$HOME/xdl9/panels; F=$HOME/xdl9/funding; RAW=$HOME/xdl/out
CHK=$HOME/xdl9/gates/tools/check_trades.py
G=$HOME/xdl9/gates/g8b/check; FROM=2465; TO=2479
mkdir -p "$G"; rm -rf "${G:?}"/*
fail=0; total=0
one() {  # метка биржа y x окно min_obs entry exit
  local tag=$1 ex=$2 y=$3 x=$4 W=$5 MO=$6 EZ=$7 XZ=$8 D=$G/$1; mkdir -p "$D"
  printf 'y,x,from_day,to_day\n%s,%s,%s,%s\n' "$y" "$x" "$FROM" "$TO" > "$D/jobs.csv"
  "$BIN" statarb run --kind pair --cell c --window $W --min-obs $MO --entry-z $EZ --exit-z $XZ --stop-z inf --max-hold 0 --refresh 0 \
    --jobs "$D/jobs.csv" --panels "$P/$ex" --funding-y "$F/$ex" --min-span 100 --threads 2 \
    --daily-out "$D/daily.csv" --trades-out "$D/trades.csv" --trade-jobs 0 --mtm-open --cells-out "$D/cells.csv" --open-out "$D/open.csv" > "$D/run.log" 2>&1 || { echo "ОШИБКА run $tag"; tail -2 "$D/run.log"; }
  echo "== G8b $tag ($ex: $y против $x; W=$W min_obs=$MO entry_z=$EZ exit_z=$XZ, без таймера и стопа): $(grep -o 'сделок [0-9]* (до окна отброшено [0-9]*)' "$D/run.log"), открыто на конце $(grep -o 'открыто на конце [0-9]*' "$D/run.log" | grep -o '[0-9]*$')"
  python3 "$CHK" --root "$RAW" --y-ex $ex --y "$y" --x-ex $ex --x "$x" --from-day $FROM --to-day $TO --rule z --window $W --min-obs $MO \
    --entry-z $EZ --exit-z $XZ --max-hold 0 --funding --trades "$D/trades.csv" --cells-out "$D/cells.csv" --open-out "$D/open.csv" --show 2 > "$D/check.log" 2>&1
  local rc=$?; total=$((total+1)); [ $rc -ne 0 ] && fail=$((fail+1))
  cat "$D/check.log"
  python3 - "$D/daily.csv" "$D/trades.csv" "$D/open.csv" <<'PY'
import csv, sys
d = sum(float(r["net_bps"]) for r in csv.DictReader(open(sys.argv[1])))
t = sum(float(r["net_bps"]) for r in csv.DictReader(open(sys.argv[2])))
o = [float(r["net_bps"]) for r in csv.DictReader(open(sys.argv[3]))]
print(f"  P&L: сумма суточного csv {d:.6f} bps, сумма сделок {t:.6f} bps (равны: {abs(d - t) < 1e-6}); открытая позиция в них НЕ входит: её оценка {sum(o):.6f} bps ({len(o)} шт.)")
PY
  echo "   rc=$rc"
}
one binance-PUMPBTC-BTC binance PUMPBTCUSDT BTCUSDT 240 120 1.0 0.0
one binance-1000000BOB-ETH binance 1000000BOBUSDT ETHUSDT 240 120 1.5 0.5
one bybit-STG-BTC bybit STGUSDT BTCUSDT 240 120 1.0 0.0
one bybit-STG-XRP bybit STGUSDT XRPUSDT 120 60 1.0 0.0
one aster-PUMPBTC-BTC-long aster PUMPBTCUSDT BTCUSDT 240 120 1.0 0.0
one aster-PUMPBTC-BTC-short aster PUMPBTCUSDT BTCUSDT 120 60 1.0 0.0
echo "ИТОГ G8b: заданий $total, с расхождениями $fail"
exit $fail
