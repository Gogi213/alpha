#!/usr/bin/env bash
# TK-291 (П-19 §6): деревья B1+touch_index (/data/tk291/out) → s-r/<мес> (симлинки) → busy-replay → r-b/<мес> → пересчёт 45 векторов
#   на месяц (tk291-recalc.py) → /data/tk291/psim/closes-p19-<мес>.json. Как tk231-p14f1-psim.sh; 9 месяцев параллельно (PM).
#   tk291-recalc.sh run   (метка /data/tk291/psim/recalc.done; сбой — psim/recalc-fail.txt)
set -u
D=/data/tk291; O=$D/psim; R=$D/out; TB=/data/tk083/tools; P=${PM:-4}
mkdir -p $O/s-r $O/r-b; cd $O || exit 2; rm -f recalc.done recalc-fail.txt
while read m d; do mkdir -p s-r/${d:0:7}; [ -e s-r/${d:0:7}/$d ] || cp -rs $R/run-$m-$d/out/$d s-r/${d:0:7}/; done < /data/tk0231/days.txt
one() { mo=$1
  [ -e r-b/$mo/busy-replay.txt ] || python3 $TB/busy-replay.py s-r/$mo r-b/$mo > busy-$mo.log 2>&1 || { echo "FAILBUSY $mo" >> recalc-fail.txt; return; }
  python3 $D/tools/tk291-recalc.py $O/r-b $mo closes-p19-$mo.json > recalc-$mo.log 2>&1 || echo "FAIL recalc $mo" >> recalc-fail.txt; }
export -f one; export O R TB D
ls s-r | sort | xargs -P "$P" -I{} bash -c 'one {}'
touch recalc.done
