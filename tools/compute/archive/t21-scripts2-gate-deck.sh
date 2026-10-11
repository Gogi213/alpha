#!/usr/bin/env bash
# T-21 batch 2: breakdown.py / equity-report.py / placebo.py / loss-atoms.py / titration-read.py /
# exit-titration-read.py до = после перехода на `_lib` (стиль — `t21-scripts-gate-deck.sh`, batch 1).
# Каталоги `tmp-t21s2/old` и `tmp-t21s2/new` — готовые копии `tools/compute/` (со своим `_lib/` по
# месту) на СТАРОМ (до этого батча) и НОВОМ коммите; их доставляет владелец задачи (git archive),
# этот скрипт только запускает и сверяет побайтно. Формулы в batch 2 не менялись — заменена только
# читка CSV (`_lib.read_csv` вместо своей копии `open`+`csv.DictReader`), так что SAME здесь и
# означает «переход безопасен».
#
# PRE-BATCH REF (коммит перед первой правкой этого батча): 6344857
#
# t32-retries/t32-epcap/t32-regime/t32-hedge/t32-exits уже сверены ЛОКАЛЬНО побайтно (данные
# data/t32/*.csv есть в репо) — деке они не нужны. t32-dump-trades.py НЕ трогался (нужен
# ~/alpha/tmp-kpi/portfolio-sim.py — снимок на деке, локально недоступен; см. отчёт задачи).
#
# Пути ниже — из докстрок скриптов и `t21-scripts-gate-deck.sh` (тот же тег/набор `titrc-u500r`,
# `t-bid-btc4h-q1`); поправить на реальные каталоги сетки/study, если они на деке другие.
cd "$HOME/alpha" || exit 1
G=tmp-t21s2
for v in old new; do
  ref=$(git -C $G/$v rev-parse HEAD 2>/dev/null || echo "не git-каталог")
  echo "$v = $ref"
done

GRID=b5/titrc-u500r/t-bid-btc4h-q1
FORM=ladder3x2..20w2-pct2-tr1x1-14400-ttl1800
TAG=titrc-u500r

for v in old new; do
  # breakdown.py — разбивка формы сетки
  nice python3 $G/$v/breakdown.py --grid-dir $GRID --form $FORM --order-usd 1000 --top 15 \
    --json $G/breakdown-$v.json > $G/breakdown-$v.txt 2> $G/breakdown-$v.log
  echo "breakdown $v rc $?"

  # equity-report.py — деньги по сетке (легаси-подпись long/short не годится без второй серии —
  # берём одну именованную серию, как в первом примере докстроки)
  nice python3 $G/$v/equity-report.py --grid-dir long=$GRID --order-usd 1000 \
    --out $G/equity-$v.html > $G/equity-$v.txt 2> $G/equity-$v.log
  echo "equity $v rc $?"

  # placebo.py — контроль «рост рынка»
  nice python3 $G/$v/placebo.py --grid-dir $GRID --form $FORM --mids study/touches \
    --csv $G/placebo-$v.csv > $G/placebo-$v.txt 2> $G/placebo-$v.log
  echo "placebo $v rc $?"

  # loss-atoms.py — атомы сделок формы
  nice python3 $G/$v/loss-atoms.py --grid-dir $GRID --form $FORM --touches study/touches \
    --approaches study/approaches/D20 --regime study/regime --root root \
    --out $G/lossatoms-$v.csv > $G/lossatoms-$v.txt 2> $G/lossatoms-$v.log
  echo "lossatoms $v rc $?"

  # titration-read.py — сводка по корзинам (тег и эпохи история/запись — как в дашборд-сборке)
  nice python3 $G/$v/titration-read.py --tag $TAG --epoch история=epochs/e-archive/study \
    --epoch запись=study --csv $G/titrread-$v.csv > $G/titrread-$v.txt 2> $G/titrread-$v.log
  echo "titrread $v rc $?"

  # exit-titration-read.py — сводка по формам выхода
  nice python3 $G/$v/exit-titration-read.py --tag $TAG --epoch история=epochs/e-archive/study \
    --epoch запись=study --csv $G/exittitrread-$v.csv > $G/exittitrread-$v.txt 2> $G/exittitrread-$v.log
  echo "exittitrread $v rc $?"
done

for c in breakdown equity placebo lossatoms titrread exittitrread; do
  for x in .txt .csv .html .json; do
    [ -f "$G/$c-old$x" ] || continue
    old_size=$(stat -c%s "$G/$c-old$x" 2>/dev/null || echo "?")
    new_size=$(stat -c%s "$G/$c-new$x" 2>/dev/null || echo "?")
    if cmp -s "$G/$c-old$x" "$G/$c-new$x"; then
      echo "$c$x SAME (${old_size}B)"
    else
      echo "$c$x DIFF (old=${old_size}B new=${new_size}B)"
    fi
  done
done
