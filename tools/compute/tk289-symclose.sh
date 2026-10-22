#!/usr/bin/env bash
# TK-289: closes с монетой ([мс, $, монета]) для сита П-18 (половина А) — по готовым запускам F1: f1 (оси 1–5,6а,7), f6b, f6v, f8/<W> (8 окон σ).
#   tk298-symclose.sh на каждый запуск: те же деревья и psim cap 0, что psim-launch.sh, только --closes-sym-out.
#   -> <запуск>/psim/closes-sym-p14f1-cap0-<мес>.json; сводка — /data/tk0289/symclose.done | symclose.fail. env PM — месяцев параллельно (9).
set -u
SC=/data/launch/out/L-P15F1/tk298-symclose.sh
R=/data/tk0289
rm -f $R/symclose.done $R/symclose.fail
for run in $R/f1 $R/f6b $R/f6v $R/f8/{60,90,120,180,360,480,720,960}; do
  PM=${PM:-9} bash $SC $run || echo "FAIL $run" >> $R/symclose.fail
done
[ -e $R/symclose.fail ] || touch $R/symclose.done
