#!/bin/bash
# TK-464: общие функции боевых прогонов tk463-sim (source). Причина пропусков bench3 (TK-463): ls каталога ev отдавал
# служебные файлы (DONE, err.log, log.txt, plan.txt); awk -F- печатал строку «DONE » с хвостовым пробелом, а xargs -L1 клеит
# такую строку со следующей -> пара «DOT 1440» уходила как аргумент и терялась (4 пары, «пар 256» вместо 252).
# pairs_make <каталог ev> <файл пар>   — только *.csv.gz, ровно 2 поля «монета ТФ», иначе отказ (rc 2)
# run_pairs <файл пар> <каталог rc> <P>  — job <монета> <ТФ> (определить до вызова) на P процессах, rc каждой пары в <монета>-<ТФ>.rc
# check_complete <файл пар> <каталог rc> <расш. вывода> <каталог вывода> — выводов = пар и все rc 0, иначе rc 1 и список
pairs_make() {
  ls "$1" | grep '\.csv\.gz$' | sed 's/\.csv\.gz$//' | awk -F- 'NF==2 && $2 ~ /^[0-9]+$/ {print $1, $2; next} {bad=1; print "BAD " $0 > "/dev/stderr"} END{exit bad}' > "$2" || return 2
  [ "$(wc -l < "$2")" = "$(ls "$1" | grep -c '\.csv\.gz$')" ] || { echo "pairs_make: строк != файлов" >&2; return 2; }
}
run_pairs() {
  mkdir -p "$2"; rm -f "$2"/*.rc
  export -f job; export RCDIR="$2"
  xargs -P "$3" -L1 bash -c 'job $0 $1; echo $? > "$RCDIR/$0-$1.rc"' < "$1"
}
check_complete() {
  local n bad=0 miss="" c tf
  n=$(wc -l < "$1")
  while read -r c tf; do
    [ "$(cat "$2/$c-$tf.rc" 2>/dev/null)" = "0" ] || { bad=1; miss="$miss $c-$tf(rc)"; continue; }
    [ -s "$4/$c-$tf.$3" ] || { bad=1; miss="$miss $c-$tf(нет вывода)"; }
  done < "$1"
  echo "полнота: пар $n, без rc0/вывода:${miss:- нет}"
  return $bad
}
