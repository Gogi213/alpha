#!/usr/bin/env bash
# TK-493 w3, G11b: независимый пересчёт из СЫРЬЯ (check_trades.py) для новых флагов: сделки, причина выхода (exit_reason),
# β на входе (beta_in), счётчики окна и z, разбивка выходов и статистика β (--ext-out) — без --rearm и с --rearm.
# Те же задания, что в g8 (8 бирж × ETH и SOL против BTC, МНК β, фандинг включён), стоп 3.5 — чтобы --rearm срабатывал.
#   g11b_check_rearm.sh
set -u
BIN=${BIN_N:-$HOME/xdl9/bin/statarb-xdl9-6108b0bf}
P=$HOME/xdl9/panels
F=$HOME/xdl9/funding
RAW=$HOME/xdl/out
CHK=${CHK:-$HOME/xdl9/gates/tools/check_trades.py}
G=${G11B_DIR:-$HOME/xdl9/gates/g11b}
FROM=2465; TO=2479
W=720; MO=360; EZ=2.0; XZ=0.5; SZ=3.5; MH=720
mkdir -p "$G"; rm -rf "${G:?}"/*
declare -A BTC=( [binance]=BTCUSDT [bybit]=BTCUSDT [aster]=BTCUSDT [bitget]=BTCUSDT [okx]=BTC-USDT-SWAP [bingx]=BTC-USDT [kraken]=PF_XBTUSD [lighter]=BTC )
declare -A ETH=( [binance]=ETHUSDT [bybit]=ETHUSDT [aster]=ETHUSDT [bitget]=ETHUSDT [okx]=ETH-USDT-SWAP [bingx]=ETH-USDT [kraken]=PF_ETHUSD [lighter]=ETH )
declare -A SOL=( [binance]=SOLUSDT [bybit]=SOLUSDT [aster]=SOLUSDT [bitget]=SOLUSDT [okx]=SOL-USDT-SWAP [bingx]=SOL-USDT [kraken]=PF_SOLUSD [lighter]=SOL )
fail=0; total=0; blocked=0
for rearm in 0 1; do
  RF=(); CF=(); [ $rearm = 1 ] && { RF=(--rearm); CF=(--rearm); }
  for ex in binance bybit okx bingx bitget kraken aster lighter; do
    for alt in ETH SOL; do
      if [ $alt = ETH ]; then Y=${ETH[$ex]}; else Y=${SOL[$ex]}; fi
      X=${BTC[$ex]}
      D=$G/r$rearm-$ex-$alt; mkdir -p "$D"
      printf 'y,x,from_day,to_day\n%s,%s,%s,%s\n' "$Y" "$X" "$FROM" "$TO" > "$D/jobs.csv"
      "$BIN" statarb run --kind pair --cell c --window $W --min-obs $MO --entry-z $EZ --exit-z $XZ --stop-z $SZ --max-hold $MH --refresh 0 "${RF[@]}" \
        --jobs "$D/jobs.csv" --panels "$P/$ex" --funding-y "$F/$ex" --min-span 1000 --threads 2 \
        --daily-out "$D/daily.csv" --trades-out "$D/trades.csv" --trade-jobs 0 --trades-ext --ext-out "$D/ext.csv" --job-day-out "$D/jd.csv" \
        --mtm-open --cells-out "$D/cells.csv" --open-out "$D/open.csv" > "$D/run.log" 2>&1 || { echo "ОШИБКА run $ex $alt"; tail -2 "$D/run.log"; }
      python3 "$CHK" --root "$RAW" --y-ex $ex --y "$Y" --x-ex $ex --x "$X" --from-day $FROM --to-day $TO --rule z --window $W --min-obs $MO \
        --entry-z $EZ --exit-z $XZ --stop-z $SZ --max-hold $MH --funding "${CF[@]}" --trades "$D/trades.csv" --cells-out "$D/cells.csv" \
        --open-out "$D/open.csv" --ext-out "$D/ext.csv" --show 0 > "$D/check.log" 2>&1
      rc=$?; total=$((total+1)); [ $rc -ne 0 ] && fail=$((fail+1))
      nb=$(python3 -c "import csv,sys; print(int(float(list(csv.DictReader(open(sys.argv[1])))[0]['rearm_blocked'])))" "$D/ext.csv")
      blocked=$((blocked+nb))
      echo "== rearm=$rearm $ex $alt: $(grep -o 'сделок в csv [0-9]*, пересчитано [0-9]* (до окна отброшено [0-9]*)' "$D/check.log"), расхождений $(grep -o 'расхождений [0-9]*' "$D/check.log" | head -1 | cut -d' ' -f2), подавлено rearm $nb, rc=$rc"
      [ $rc -ne 0 ] && cat "$D/check.log"
      grep 'ext-out run' "$D/check.log"
    done
  done
done
echo "ИТОГ G11b: заданий $total, с расхождениями $fail, подавлено --rearm сигналов (сумма) $blocked"
exit $fail
