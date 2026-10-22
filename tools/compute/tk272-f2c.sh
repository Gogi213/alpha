#!/usr/bin/env bash
# TK-272 F2c: 22 хеша зоны §11.2 (Г-10: окна 1/2/6/8/12 ч x q9 и окно 9 ч x q5/6/8/9/10; Г-112: W5/6/10/15/30/60/120/300 x q92 и W8 x q90/91/92/95) x 9 мес.
# боевым путём формы; режим с колонкой btc_ret_9h_bps (RG), бинарь PGO tk272b (796fdba3, гейт 1017181158553: 41 md5 identical, В-21), пороги v3/v9.
# rc: 3 fail.txt не пуст · 5 нет psim-p14f1.done
export REASON="TK-272 F2c: боевой PGO-бинарь tk272b (гейт 1017181158553 байт в байт)" NX=${NX:-12} GT=1 BIN=/opt/alpha-compute/bin/alpha-tk272b-pgo SCRIPTS=/data/tk0231 RG=/data/tk0272/regime9h
R=/data/tk0272/f2c
cp /data/tk0272/run2/days.txt $R/days.txt
bash /opt/alpha-launch/tools/launch/run-launch.sh $R > /data/tk0272/f2c.out 2>&1
rc=$?
echo $rc > /data/tk0272/f2c.rc
n=$(cat $R/out/*/fail.txt 2>/dev/null | wc -l)
[ "$n" = 0 ] || { echo "fail.txt: $n строк" >> /data/tk0272/f2c.out; rc=3; }
[ -e $R/psim/psim-p14f1.done ] || rc=5
exit $rc
