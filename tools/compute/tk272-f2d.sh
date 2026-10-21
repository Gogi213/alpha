#!/usr/bin/env bash
# TK-272 F2d: ВСЕ клетки зоны §11.2 Г-10/Г-112, ещё не посчитанные (121 хеш: Г-10 20 = окна 1…12 ч x q5…q10 (в т.ч. 10/11 ч), Г-112 101 = 25 окон W x q90…q95 (q94, 16 новых W)) x 9 мес.
# одним --cells на сутки (Судья 17.10 23:43: считать все 99 вне домена; f2c 22 хеша — подмножество, заменён). Боевой путь формы; режим RG=regime11h (btc_ret_9/10/11h_bps),
# бинарь PGO tk272d (гейт 1018003057550: 41 md5 identical к tk272c, tk272c к tk272b — В-21); пороги v11 / BTC v4 (v9 / v3 побитно внутри).
# rc: 3 fail.txt не пуст · 5 нет psim-p14f1.done
export REASON="TK-272 F2d: боевой PGO-бинарь tk272d (гейт 1018003057550 байт в байт)" NX=${NX:-12} GT=1 BIN=/opt/alpha-compute/bin/alpha-tk272d-pgo SCRIPTS=/data/tk0231 RG=/data/tk0272/regime11h
R=/data/tk0272/f2d
cp /data/tk0272/run2/days.txt $R/days.txt
bash /opt/alpha-launch/tools/launch/run-launch.sh $R > /data/tk0272/f2d.out 2>&1
rc=$?
echo $rc > /data/tk0272/f2d.rc
n=$(cat $R/out/*/fail.txt 2>/dev/null | wc -l)
[ "$n" = 0 ] || { echo "fail.txt: $n строк" >> /data/tk0272/f2d.out; rc=3; }
[ -e $R/psim/psim-p14f1.done ] || rc=5
exit $rc
