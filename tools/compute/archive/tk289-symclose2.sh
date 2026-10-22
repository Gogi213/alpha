#!/usr/bin/env bash
# TK-289: closes с монетой для сита F2 П-18 (половина А) — по f2 (123 кл.) и f245 (18 кл.); как symclose.sh, psim cap 0 без движка.
#   -> <запуск>/psim/closes-sym-p14f1-cap0-<мес>.json; сводка — /data/tk0289/symclose2.done | symclose2.fail. env PM (9).
set -u
SC=/data/launch/out/L-P15F1/tk298-symclose.sh
R=/data/tk0289
rm -f $R/symclose2.done $R/symclose2.fail
for run in $R/f2 $R/f245; do
  PM=${PM:-9} bash $SC $run || echo "FAIL $run" >> $R/symclose2.fail
done
[ -e $R/symclose2.fail ] || touch $R/symclose2.done