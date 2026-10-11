#!/bin/bash
# TK-120: сделки R2-B (31 клетка × B1-сутки янв–сен, /data/p12r3a/r2b/<сутки>/out/<сутки>) -> деревья по месяцу -> busy-replay -> portfolio-sim cap 0 и 3
# с --drop TRUMP/TRX/BCH (v171c, В-210) -> /data/p12r3a/closes-r2bd-cap<N>-<мес>.json (имена форм месячные: Q из порогов месяца; порядок клеток = cells.txt).
# Равная экспозиция g95 sw4/sw5 (§6(2)) — следующим шагом. Без пересчёта R2-B (В-213). Метка psim-r2b.done; ждёт done всех 251 суток (до 6 ч).
set -u
O=/data/p12r3a; R=$O/r2b; TB=/data/tk083/tools; T=/data/tk0113/tools; DROP=TRUMPUSDT,TRXUSDT,BCHUSDT; S=t-bid-btc4h-q1
cd $O || exit 2; rm -f psim-r2b.done r2b-fail.txt
for i in $(seq 1 720); do n=$(ls $R/2*/done 2>/dev/null | wc -l); [ "$n" -ge 251 ] && break; sleep 30; done
[ "$n" -ge 251 ] || { echo "WAIT_TIMEOUT done=$n" >> r2b-fail.txt; exit 3; }
cat $R/2*/fail.txt | grep . >> r2b-fail.txt && exit 4
while read d m; do
  [ -e $R/$d/done ] || continue
  mkdir -p s-r/${d:0:7}; [ -e s-r/${d:0:7}/$d ] || cp -rs $R/$d/out/$d s-r/${d:0:7}/
done < /data/tk065/days/days.tsv
for mo in $(ls s-r | sort); do
  [ -e r-b/$mo/busy-replay.txt ] || python3 $TB/busy-replay.py s-r/$mo r-b/$mo > busy-r-$mo.log 2>&1 || { echo "FAILBUSY $mo" >> r2b-fail.txt; continue; }
  d1=$(ls s-r/$mo | head -1); args=()
  while read f s; do args+=(--variant "$s@$f=$s/$f"); done < $R/$d1/cells.txt
  for cap in 0 3; do
    python3 $T/portfolio-sim.py --epoch "$mo=$O/r-b:$mo" "${args[@]}" --drop $DROP --deposit-usd 2500 --position-usd 500 --max-pos $cap \
      --closes-out closes-r2bd-cap$cap-$mo.json > psim-r2bd-cap$cap-$mo.txt 2> psim-r2bd-cap$cap-$mo.log || echo "FAIL r2bd $cap $mo" >> r2b-fail.txt
  done
done
touch psim-r2b.done
