#!/usr/bin/env bash
# TK-493 F6, G8: сделки `statarb run` пересчитываются из СЫРЬЯ (csv.gz) независимым скриптом check_trades.py.
# Запуск на Steam Deck. По каждой из 8 минутных бирж: пара «альт против BTC той же биржи» (вид A2, β — МНК, фандинг ВКЛЮЧЁН,
# оба ряда из одного каталога ставок), два задания (ETH и SOL против BTC), окно — ФОРМИРОВАНИЕ (сутки 2465..2478).
# Для каждого задания пересчитываются ВСЕ его сделки (β, сигнал, исполнение, комиссия, фандинг, net) и счётчики run.
#   g8_trades.sh
set -u
BIN=$HOME/xdl9/bin/statarb-xdl9-02c6a0df
P=$HOME/xdl9/panels
F=$HOME/xdl9/funding
RAW=$HOME/xdl/out
CHK=$HOME/xdl9/gates/tools/check_trades.py
G=$HOME/xdl9/gates/g8
FROM=2465; TO=2479
W=720; MO=360; EZ=2.0; XZ=0.5; SZ=3.5; MH=720
mkdir -p "$G"; rm -rf "${G:?}"/*
declare -A BTC=( [binance]=BTCUSDT [bybit]=BTCUSDT [aster]=BTCUSDT [bitget]=BTCUSDT [okx]=BTC-USDT-SWAP [bingx]=BTC-USDT [kraken]=PF_XBTUSD [lighter]=BTC )
declare -A ETH=( [binance]=ETHUSDT [bybit]=ETHUSDT [aster]=ETHUSDT [bitget]=ETHUSDT [okx]=ETH-USDT-SWAP [bingx]=ETH-USDT [kraken]=PF_ETHUSD [lighter]=ETH )
declare -A SOL=( [binance]=SOLUSDT [bybit]=SOLUSDT [aster]=SOLUSDT [bitget]=SOLUSDT [okx]=SOL-USDT-SWAP [bingx]=SOL-USDT [kraken]=PF_SOLUSD [lighter]=SOL )
fail=0; total=0
for ex in binance bybit okx bingx bitget kraken aster lighter; do
  for alt in ETH SOL; do
    if [ $alt = ETH ]; then Y=${ETH[$ex]}; else Y=${SOL[$ex]}; fi
    X=${BTC[$ex]}
    D=$G/$ex-$alt; mkdir -p "$D"
    printf 'y,x,from_day,to_day\n%s,%s,%s,%s\n' "$Y" "$X" "$FROM" "$TO" > "$D/jobs.csv"
    "$BIN" statarb run --kind pair --cell c --window $W --min-obs $MO --entry-z $EZ --exit-z $XZ --stop-z $SZ --max-hold $MH --refresh 0 \
      --jobs "$D/jobs.csv" --panels "$P/$ex" --funding-y "$F/$ex" --min-span 1000 --threads 2 \
      --daily-out "$D/daily.csv" --trades-out "$D/trades.csv" --trade-jobs 0 \
      --mtm-open --cells-out "$D/cells.csv" --open-out "$D/open.csv" > "$D/run.log" 2>&1 || { echo "ОШИБКА run $ex $alt"; tail -2 "$D/run.log"; }
    echo "== $ex $alt: $(grep -o 'фандинг: [^,]*' "$D/run.log" | head -1); $(grep -o 'сделок [0-9]* (до окна отброшено [0-9]*)' "$D/run.log")"
    python3 "$CHK" --root "$RAW" --y-ex $ex --y "$Y" --x-ex $ex --x "$X" --from-day $FROM --to-day $TO --rule z --window $W --min-obs $MO \
      --entry-z $EZ --exit-z $XZ --stop-z $SZ --max-hold $MH --funding --trades "$D/trades.csv" --cells-out "$D/cells.csv" --open-out "$D/open.csv" --show 3 > "$D/check.log" 2>&1
    rc=$?; total=$((total+1)); [ $rc -ne 0 ] && fail=$((fail+1))
    cat "$D/check.log"
    echo "   rc=$rc"
  done
done
echo "ИТОГ G8: заданий $total, с расхождениями $fail"
exit $fail
