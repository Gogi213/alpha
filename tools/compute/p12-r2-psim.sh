#!/bin/bash
# П-12 R2: деревья суток волны TK-065 (w-/wt-, busy-skip on движка) -> по месяцу -> portfolio-sim (без потолка и B2 = 3) -> /data/p12r2/
# Без досчёта 8 суток TK-040 (TRX и др.): добавляется отдельным шагом. Запуск: p12-r2-psim.sh (юнитом alsched, 1 ядро).
set -u
O=/data/p12r2; T=/data/tk083/tools; D=/data/tk065
mkdir -p $O; cd $O || exit 2; rm -f psim.done
while read d m; do
  mo=${d:0:7}
  mkdir -p v/$mo vt/$mo
  [ -e v/$mo/$d ] || ln -s $D/w-$d/b5/r2/$d v/$mo/$d
  [ -e vt/$mo/$d ] || ln -s $D/wt-$d/b5/r2t/$d vt/$mo/$d
done < $D/days/days.tsv
for mo in $(ls v | sort); do
  for kind in v vt; do
    cells=$D/days/r2-2026-03-10.cells; [ $kind = vt ] && cells=$D/days/r2t-2026-03-10.cells
    args=(); while read f s; do args+=(--variant "$s@$f=$s/$f"); done < $cells
    for cap in 0 3; do
      python3 $T/portfolio-sim.py --epoch "$mo=$O/$kind:$mo" "${args[@]}" --deposit-usd 2500 --position-usd 500 --max-pos $cap \
        --json psim-$kind-cap$cap-$mo.json --closes-out closes-$kind-cap$cap-$mo.json > psim-$kind-cap$cap-$mo.txt 2> psim-$kind-cap$cap-$mo.log || echo "FAIL $kind $cap $mo" >> fail.txt
    done
  done
done
touch psim.done
