#!/usr/bin/env bash
# TK-291 (П-19 §4, П-14 §11.2): F2 — 24 вектора по tk291-f2vec.py (f2vec.txt) на деревьях r-b (уже после busy-replay) →
#   /data/tk291/psim/f2/closes-p19-<мес>.json = F1 ∪ F2 (--merge). 9 месяцев параллельно (PM). Метка psim/f2/f2.done; сбой — f2/fail.txt.
set -u
D=/data/tk291; O=$D/psim; P=${PM:-4}
mkdir -p $O/f2; cd $O || exit 2; rm -f f2/f2.done f2/fail.txt
one() { mo=$1
  python3 $D/tools/tk291-recalc.py $O/r-b $mo f2/closes-p19-$mo.json --vecfile $O/f2vec.txt --merge $O/closes-p19-$mo.json > f2/recalc-$mo.log 2>&1 || echo "FAIL $mo" >> f2/fail.txt; }
export -f one; export O D
ls r-b | sort | xargs -P "$P" -I{} bash -c 'one {}'
touch f2/f2.done
