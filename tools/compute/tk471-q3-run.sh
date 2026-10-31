#!/bin/bash
# TK-471: Q-3 в ядре — D2b только для ячеек, прошедших Холм против C-X со знаком Δ>0 (1622 варианта: Санчо 345, bp 831 = 111 + 720, S1-S5 446),
# с новыми столбцами bt, bm (net при всех ногах тейкер / мейкер; ядро tk471-pair-sim --legs 1). Четыре набора подряд, каждый по 15 процессов.
# bash tk471-q3-run.sh    выход /data/tk0471/q3-out/{sancho,bp,bp3,s}/<SYM>-<tf>.csv.gz, метка /data/tk0471/q3-out/DONE
# Ячейки: tk471_cells.py (подмножество из docs/findings/sancho-bp-exits-2026-10-31.csv) -> /data/tk0471/cells-{sancho,bp,bp3,s}
set -u
W=/data/tk0461; T=/data/tk0471; O=$T/q3-out; mkdir -p $O; rm -f $O/DONE
printf '#!/bin/bash\nexec %s "$@" --legs 1\n' $T/bin/tk471-pair-sim > $T/bin/tk471-legs.sh; chmod +x $T/bin/tk471-legs.sh
run() { BIN=$T/bin/tk471-legs.sh EV=$2 CELLS=$T/cells-$1 OUT=$O/$1 NX=${NX:-15} bash /data/tk0469/tk461-d2b-run-bp.sh; cat $O/$1/DONE; }
run sancho $W/d2b-ev
run bp $W/d2b-ev-bp2
run bp3 $W/d2b-ev-bp3
run s $W/d2b-ev-s
echo "готово $(date +%H:%M)" > $O/DONE
