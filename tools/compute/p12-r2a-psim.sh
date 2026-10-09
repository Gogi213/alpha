#!/bin/bash
# П-12 R2 (TK-063): пересчёт семей A (pyre/pynw/pyeat/pyfresh) по волне A TK-065 (wa-*/b5/r2a, правка 6b792282):
# деревья -> по месяцу -> busy-replay -> portfolio-sim (cap 0 и 3) -> /data/p12r2a/closes-va-cap<N>-<мес>.json (только A-клетки; база и не-A — из /data/p12r2).
# Запуск: p12-r2a-psim.sh (юнитом alsched, 1 ядро). Метка psim.done.
set -u
O=/data/p12r2a; T=/data/tk083/tools; D=/data/tk065
mkdir -p $O; cd $O || exit 2; rm -f psim.done fail.txt
while read d m; do
  mo=${d:0:7}
  mkdir -p s-a/$mo
  [ -e s-a/$mo/$d ] || cp -rs $D/wa-$d/b5/r2a/$d s-a/$mo/
done < $D/days/days.tsv
for mo in $(ls s-a | sort); do
  [ -e a-b/$mo/busy-replay.txt ] || python3 $T/busy-replay.py s-a/$mo a-b/$mo > busy-a-$mo.log 2>&1 || { echo "FAILBUSY $mo" >> fail.txt; continue; }
  args=(); while read f s; do args+=(--variant "$s@$f=$s/$f"); done < $D/days/r2a-2026-03-10.cells
  for cap in 0 3; do
    python3 $T/portfolio-sim.py --epoch "$mo=$O/a-b:$mo" "${args[@]}" --deposit-usd 2500 --position-usd 500 --max-pos $cap \
      --json psim-a-cap$cap-$mo.json --closes-out closes-va-cap$cap-$mo.json > psim-a-cap$cap-$mo.txt 2> psim-a-cap$cap-$mo.log || echo "FAIL $cap $mo" >> fail.txt
  done
done
touch psim.done
