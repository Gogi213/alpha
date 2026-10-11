#!/bin/bash
# TK-042: ход tk042-run в /data/progress/tk042.json раз в 30 с (done — по строкам журналов шага)
D=/data/tk037/tk042
while :; do
  s=$(tail -1 $D/steps.log | cut -c1)
  case $s in
    A) alpha-progress tk042 TK-042 "A докачка 97 суток" $(wc -l < $D/one.log) 97 сут "B verify+validate сентября 11 монет";;
    B) alpha-progress tk042 TK-042 "B verify+validate сентября" $(wc -l < $D/b.log 2>/dev/null || echo 0) 11 монет "C validate старых эпох";;
    C) alpha-progress tk042 TK-042 "C validate старых эпох" $(wc -l < $D/c.log 2>/dev/null || echo 0) $(ls $D/ep | wc -l) "эпоха-монета" "проверка полноты TK-041";;
  esac
  [ -e $D/ALL_DONE ] && { alpha-progress tk042 TK-042 "готово" 1 1 шаг "проверка полноты"; exit 0; }
  sleep 30
done
