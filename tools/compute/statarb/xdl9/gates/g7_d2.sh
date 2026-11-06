#!/usr/bin/env bash
# TK-493 F6, G7: фандинг в сделках правила D2 (Rule::Funding, вид funding-pp, β = 1) пересчитывается из СЫРЬЯ (candles + funding csv.gz)
# независимым check_trades.py: сигнал по трейлинг-среднему k выплат ноги Y, сторона, исполнение, выход, комиссия, выплаты обеих ног.
# Пары: Binance/Bybit (оба 8 ч), OKX/BingX (оба 8 ч), Kraken (1 ч) / Bitget (8 ч), Lighter (1 ч) / Aster (8 ч) — разные интервалы и единицы.
# Окно — формирование (сутки 2465..2478). Значения k, hold, max_hold — для проверки механики, не выбор исследования.
set -u
BIN=$HOME/xdl9/bin/statarb-xdl9-02c6a0df
P=$HOME/xdl9/panels; F=$HOME/xdl9/funding; RAW=$HOME/xdl/out
CHK=$HOME/xdl9/gates/tools/check_trades.py
G=${GDIR:-$HOME/xdl9/gates/g7d2}; FROM=2465; TO=2479
K=${K:-3}; H=${H:-40}; MH=${MH:-1440}
mkdir -p "$G"; rm -rf "${G:?}"/*
fail=0; total=0
run_pair() {  # метка yex y xex x
  local tag=$1 yex=$2 y=$3 xex=$4 x=$5 D=$G/$1; mkdir -p "$D"
  printf 'y,x,from_day,to_day\n%s,%s,%s,%s\n' "$y" "$x" "$FROM" "$TO" > "$D/jobs.csv"
  "$BIN" statarb run --kind funding-pp --cell d2 --rule funding --funding-k $K --hold-payments $H \
    --window 720 --min-obs 360 --entry-z 2.0 --exit-z 0.5 --stop-z inf --max-hold $MH --refresh 0 \
    --jobs "$D/jobs.csv" --panels "$P/$yex" --panels-b "$P/$xex" --funding-y "$F/$yex" --funding-x "$F/$xex" --min-span 1000 --threads 2 \
    --daily-out "$D/daily.csv" --trades-out "$D/trades.csv" --trade-jobs 0 --mtm-open --cells-out "$D/cells.csv" --open-out "$D/open.csv" > "$D/run.log" 2>&1 || { echo "ОШИБКА run $tag"; tail -2 "$D/run.log"; }
  echo "== D2 $tag ($yex/$y против $xex/$x, k=$K, hold=$H, max_hold=$MH): $(grep -o 'фандинг: .*' "$D/run.log" | head -1); $(grep -o 'сделок [0-9]* (до окна отброшено [0-9]*)' "$D/run.log")"
  python3 "$CHK" --root "$RAW" --y-ex $yex --y "$y" --x-ex $xex --x "$x" --from-day $FROM --to-day $TO --rule funding --funding-k $K --hold-payments $H \
    --window 720 --min-obs 360 --max-hold $MH --funding --trades "$D/trades.csv" --cells-out "$D/cells.csv" --open-out "$D/open.csv" --show 4 > "$D/check.log" 2>&1
  local rc=$?; total=$((total+1)); [ $rc -ne 0 ] && fail=$((fail+1))
  cat "$D/check.log"; echo "   rc=$rc"
}
run_pair binance-bybit-BTC  binance BTCUSDT bybit BTCUSDT
run_pair binance-bybit-SOL  binance SOLUSDT bybit SOLUSDT
run_pair okx-bingx-BTC      okx BTC-USDT-SWAP bingx BTC-USDT
run_pair kraken-bitget-BTC  kraken PF_XBTUSD bitget BTCUSDT
run_pair lighter-aster-BTC  lighter BTC aster BTCUSDT
run_pair kraken-lighter-ETH kraken PF_ETHUSD lighter ETH
echo "ИТОГ G7-D2: заданий $total, с расхождениями $fail"
exit $fail
