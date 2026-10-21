#!/usr/bin/env bash
# TK-289: closes с монетой для сита F2 осей 6б и 8 (половина А) — по f2b/b6 и f2b/<окно σ> (12 кл.); как symclose2.sh, psim cap 0 без движка.
#   -> <запуск>/psim/closes-sym-p14f1-cap0-<мес>.json; сводка — /data/tk0289/symclose3.done | symclose3.fail. env PM (9).
set -u
SC=/data/launch/out/L-P15F1/tk298-symclose.sh
R=/data/tk0289
rm -f $R/symclose3.done $R/symclose3.fail
for run in $R/f2b/b6 $R/f2b/{266,294,325,387,416,447}; do
  PM=${PM:-9} bash $SC $run || echo "FAIL $run" >> $R/symclose3.fail
done
[ -e $R/symclose3.fail ] || touch $R/symclose3.done
