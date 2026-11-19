#!/bin/bash
# $1 префикс юнитов, $2 биржа (bybit); недостающие ступени сетки и расширение сверх 320 до излома (В-144): по юниту на вид×группу, 3 повтора подряд.
# Виды book/trade/tick — один тип потока на соединение; all — три типа поровну (n потоков = n/3 символов), для набора «всё в одном соединении».
# Готовые файлы не пересчитываются (ladder.sh); сырьё /data/bbws-ladder; метка DONE-<юнит> по окончании.
P=$1; X=${2:-bybit}; H=$(cd "$(dirname "$0")" && pwd); D=/data/bbws-ladder
for k in book trade tick; do for g in top mid; do
  S="10 20 40 80 160 320 640"; [ $g = top ] && S="$S 822"
  systemd-run --unit=$P-$k-$g --collect -q -E STEPS="$S" bash -c "for r in 1 2 3; do $H/ladder.sh $k $g \$r $X; done; touch $D/DONE-$P-$k-$g"
done; done
systemd-run --unit=$P-all-top --collect -q -E STEPS="960 1920 2466" bash -c "for r in 1 2 3; do $H/ladder.sh all top \$r $X; done; touch $D/DONE-$P-all-top"
