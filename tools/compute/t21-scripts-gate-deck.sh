#!/usr/bin/env bash
# T-21 batch 1: exit-sim.py / loss-days.py / family-titrate.py до = после перехода на `_lib`
# (см. `t21-psim-gate-deck.sh` — тот же стиль). Каталоги `tmp-t21s/old` и `tmp-t21s/new` — готовые
# копии `tools/compute/` (со своим `_lib/`, `portfolio-sim.py`, эпохами/свечами по месту) на старом и
# новом коммите; их доставляет владелец задачи, этот скрипт только запускает и сверяет побайтно.
# В отличие от `t21-psim-gate-deck.sh` (JSON с `generated_utc`) эти три скрипта такого поля не пишут
# — `cmp` голых `.txt`/`.csv` достаточен, нормализатор не нужен.
cd "$HOME/alpha" || exit 1
G=tmp-t21s
for v in old new; do
  ref=$(git -C $G/$v rev-parse HEAD 2>/dev/null || echo "не git-каталог")
  echo "$v = $ref"
done

EPOCHS=(--epoch "август=epochs/e-aug:b5/titrc-u500r" --epoch "сентябрь=epochs/e-archive:b5/titrc-u500r" --epoch "сентябрь=.:b5/titrc-u500r")
KLINES=(--klines "август=epochs/e-aug/study/klines" --klines "сентябрь=study/klines")
SETFORM=(--set t-bid-btc4h-q1 --form ladder3x2..20w2-pct2-tr1x1-14400-ttl1800)

for v in old new; do
  # exit-sim.py — два семейства (E29 трейлы/люстра/разгон, E28 стоп по волатильности/BTC)
  nice python3 $G/$v/exit-sim.py --family e29 "${EPOCHS[@]}" "${KLINES[@]}" "${SETFORM[@]}" --drop TRXUSDT \
    --trades-csv $G/exitsim-e29-$v.csv > $G/exitsim-e29-$v.txt 2> $G/exitsim-e29-$v.log
  echo "exitsim-e29 $v rc $?"
  nice python3 $G/$v/exit-sim.py --family e28 "${EPOCHS[@]}" "${KLINES[@]}" "${SETFORM[@]}" --drop TRXUSDT \
    --trades-csv $G/exitsim-e28-$v.csv > $G/exitsim-e28-$v.txt 2> $G/exitsim-e28-$v.log
  echo "exitsim-e28 $v rc $?"

  # loss-days.py
  nice python3 $G/$v/loss-days.py "${EPOCHS[@]}" "${SETFORM[@]}" --drop TRXUSDT \
    --csv $G/lossdays-$v.csv > $G/lossdays-$v.txt 2> $G/lossdays-$v.log
  echo "lossdays $v rc $?"

  # family-titrate.py — оба разбиения на семейства (rv24h по умолчанию, grade — E33)
  nice python3 $G/$v/family-titrate.py "${EPOCHS[@]}" "${KLINES[@]}" "${SETFORM[@]}" --drop TRXUSDT \
    > $G/famtitr-rv24h-$v.txt 2> $G/famtitr-rv24h-$v.log
  echo "famtitr-rv24h $v rc $?"
  nice python3 $G/$v/family-titrate.py "${EPOCHS[@]}" "${KLINES[@]}" "${SETFORM[@]}" --drop TRXUSDT --family-by grade \
    > $G/famtitr-grade-$v.txt 2> $G/famtitr-grade-$v.log
  echo "famtitr-grade $v rc $?"
done

for c in exitsim-e29 exitsim-e28 lossdays famtitr-rv24h famtitr-grade; do
  for x in .txt .csv; do
    [ -f "$G/$c-old$x" ] || continue
    cmp -s "$G/$c-old$x" "$G/$c-new$x" && echo "$c$x SAME" || echo "$c$x DIFF"
  done
done
# Итог 28.09 (дека, alpha-t21-scripts-gate, 3:14 CPU): old = 08d3c60~1, new = 8acb544 (архивы tools/compute);
# все 8 выходов SAME, непустые (txt 5–31 КБ, csv 0,1–0,8 МБ), md5 exit-sim old/new различны — гейт не пустой.
