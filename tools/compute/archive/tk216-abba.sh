#!/bin/bash
# TK-216: ABBA стены волны F1 01–15.01 (A = bounce-grid по клеткам, B = пул + раннер); итог — /data/tk216/wall/abba.{txt,done}
# СТЕНД ВЫВЕДЕН 16.10, В-20 (TK-262): мерка «волна F1 01–15.01» не используется — «было/стало» мерится tools/compute/bs-launch.sh (боевой путь формы). Скрипт оставлен как история TK-216.
mkdir -p /data/tk216/wall; cd /data/tk216
for x in A:a1 B:b1 B:b2 A:a2; do
  /data/benchrun.sh wave bash /data/tk216/wall.sh ${x%%:*} ${x##*:} > /data/tk216/wall/bench-${x##*:}.log 2>&1
  echo "${x} rc=$?" >> /data/tk216/wall/abba.txt
done
touch /data/tk216/wall/abba.done
