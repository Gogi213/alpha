#!/usr/bin/env bash
# T-21: portfolio-sim до (e7e8379~1) = после (e7e8379) на реальных эпохах дашборда; побайтно JSON и closes.
cd "$HOME/alpha" || exit 1
G=tmp-t21g
EXCL=$(grep -v "^#" study/pump-exclude.csv | tail -n +2 | paste -sd,)
FUND=$(ls study/funding/*.csv 2>/dev/null | head -1)
COMMON=(--epoch "история=epochs/e-archive:b5/titrc-u500r" --epoch "запись=.:b5/titrc-u500r" --epoch "август=epochs/e-aug:b5/titrc-u500r"
  --epoch "обвал=epochs/e-crash:b5/crash-u500r" --join "сентябрь=история+запись" --join "август+сентябрь=август+история+запись"
  --klines study/klines --klines epochs/e-aug/study/klines --klines epochs/e-crash/study/klines
  --variant "BTC 4 ч: трейл 1/1=t-bid-btc4h-q1/ladder3x2..20w2-pct2-tr1x1-14400-ttl1800"
  --variant "Без фильтра просадки=t-bid-age-45/ladder3x2..20w2-pct2-tr1x1-14400-ttl1800"
  --deposit-usd 2500 --position-usd 500 --exclude-set "прокиды=$EXCL" --drop TRXUSDT)
run() { local v=$1 c=$2; shift 2; nice python3 $G/$v/portfolio-sim.py "${COMMON[@]}" "$@" --json $G/$c-$v.json --closes-out $G/$c-$v-closes.json > $G/$c-$v.txt 2> $G/$c-$v.log; echo "$c $v rc $?"; }
for v in old new; do
  run $v dash --max-pos 0,3 --day-stop-pct 0 --btc-kill-bps 0,150
  run $v rules --max-pos 0,5 --day-stop-pct 0,2 --btc-kill-bps 150 --streak-stop 5 --size-mult 1.5 ${FUND:+--funding $FUND}
done
for c in dash rules; do
  for x in .json -closes.json .txt; do cmp -s $G/$c-old$x $G/$c-new$x && echo "$c$x SAME" || echo "$c$x DIFF"; done
done
echo "funding: ${FUND:-нет}"
# Итог 27.09 (дека, alpha-t21-psim-gate): closes и .txt — SAME в обоих прогонах; .json отличается только полем
# generated_utc (минута запуска) — побайтный разбор по ключам, остальное равно. Старое = e7e8379~1, новое = e7e8379.
