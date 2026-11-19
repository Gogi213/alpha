#!/usr/bin/env bash
# G-022 pool-fix: дополнительные проходы по окнам лестницы `top10_ladder.sh` (после неё, п. 23: на хосте во время окон ничего постороннего).
#   top10_extra.sh <binance|bybit> <метка лестницы>
# По каждому окну ступеней `10` и `full`: symlag.py — записи в секунду и задержка прихода «топ-10 против остальных» из файлов самой копии (одно окно, один процесс — условия равны по построению);
# для `full` ещё restcheck.py по 10 самым оборотистым по числу сделок символам окна (REST-сверка сделок именно тех символов, что раньше исключались; в лестнице сверка шла по 25 равномерно выбранным).
set -u
EX="$1"; TAG="$2"
S=${TOOLS:-/data/turbo-probe/src/tools/turbo}; PY=/root/bnws-venv/bin/python
D=/data/turbo-probe/$TAG
TOP=$($PY -c "import json; print(','.join(json.load(open('$D/pool-full.orig.json'))['top10']))")
for NP in 10 full; do
  for K in 1 2 3; do
    W="$D/$TAG-n$NP-w$K"
    [ -s "$W/t0-ms.txt" ] || continue
    T0=$(cat "$W/t0-ms.txt")
    read -r TC NS < "$W/rest-args.txt"
    $PY "$S/symlag.py" "$W/data" "$T0" --top "$TOP" --out "$W/symlag.json" > /dev/null
    # объём минутных свечей биржи против записанных сделок: независимый эталон и там, где у REST нет истории сделок (Bybit — только последние 1000 на символ)
    $PY "$S/klinecheck.py" "$EX" "$W/data" "$T0" --symbols "$TOP" --covered-ms "$TC" --out "$W/kline.json" > "$W/kline.log" 2>&1
    if [ "$NP" = full ]; then
      $PY "$S/restcheck.py" "$EX" "$W/data" "$T0" "$(( T0 + 180000 ))" --symbols 10 --covered-ms "$TC" --out "$W/rest-top10.json" > "$W/rest-top10.log" 2>&1
    fi
    echo "$W done"
  done
done
echo "ALLDONE" > "$D/extra.done"
