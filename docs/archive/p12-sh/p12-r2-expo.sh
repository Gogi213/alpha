#!/bin/bash
# П-12 R2 (TK-063): равная экспозиция §6(2) + срабатывания §8: p12-r2-expo.py -> vn-b/vtn-b -> portfolio-sim (cap 0 и 3) -> closes-vn-*/closes-vtn-*; метка expo.done
set -u
O=/data/p12r2; T=/data/tk083/tools; D=/data/tk065; cd $O || exit 2; rm -f expo.done
python3 $O/p12-r2-expo.py $O > expo.log 2>&1 || { echo FAILEXPO >> fail.txt; exit 1; }
for mo in $(ls vn-b | sort); do
  for kind in vn vtn; do
    [ -d $kind-b/$mo ] || continue
    cells=$D/days/r2-2026-03-10.cells; [ $kind = vtn ] && cells=$D/days/r2t-2026-03-10.cells
    args=(); while read f s; do args+=(--variant "$s@$f=$s/$f"); done < $cells
    for cap in 0 3; do
      python3 $T/portfolio-sim.py --epoch "$mo=$O/$kind-b:$mo" "${args[@]}" --deposit-usd 2500 --position-usd 500 --max-pos $cap \
        --json psim-$kind-cap$cap-$mo.json --closes-out closes-$kind-cap$cap-$mo.json > psim-$kind-cap$cap-$mo.txt 2> psim-$kind-cap$cap-$mo.log || echo "FAIL $kind $cap $mo" >> fail.txt
    done
  done
done
touch expo.done
