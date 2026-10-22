#!/bin/bash
# TK-278 п.2: нотионал М целиком — 90 клеток деревьев TK-196 (tk278-expo.sh) + 25 старых (tk278-run-old.sh, со сверкой с p12r2a/tk194). Итог — /data/tk0278/m-all.rc (bad=<n>).
H=$(dirname "$(readlink -f "$0")"); rm -f /data/tk0278/m-all.rc; bad=0
bash $H/tk278-expo.sh; [ -e /data/tk0278/expo/m.done ] || bad=$((bad+1))
bash $H/tk278-run-old.sh || bad=$((bad+1))
echo "bad=$bad" > /data/tk0278/m-all.rc; exit $bad
