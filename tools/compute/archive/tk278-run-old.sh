#!/bin/bash
# TK-278 п.2 (остаток): старые клетки М -> closes с монетой + сверка. Итог — /data/tk0278/expo-old/rc.txt.
W=/data/tk0278/expo-old; H=$(dirname "$(readlink -f "$0")"); mkdir -p $W; rm -f $W/rc.txt
bash $H/tk278-expo-old.sh; bad=0; [ -e $W/old.done ] || bad=1
python3 $H/tk278-expo-old-cmp.py > $W/cmp.txt 2>&1 || bad=$((bad+1))
echo "bad=$bad" > $W/rc.txt; exit $bad
