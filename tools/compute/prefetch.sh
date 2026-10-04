#!/usr/bin/env bash
# Последовательный предзагрузчик: читает файлы следующих единиц (корень монета) в page cache на AHEAD единиц вперёд
# от работающих; готовность единицы = w-*.csv в каталоге результатов. Запуск: prefetch.sh units.txt rundir P [AHEAD]
set -u
U=$1; R=$2; P=$3; A=${4:-3}
i=0
while read -r d s _; do
  i=$((i+1))
  tag=$(echo "$d" | sed 's#^/data/##; s#/#_#g')
  [ -s "$R/w-$tag-$s.csv" ] && continue
  while :; do
    done_n=$(ls "$R"/w-*.csv 2>/dev/null | wc -l)
    [ "$i" -le $((done_n + P + A)) ] && break
    sleep 5
  done
  cat "$d/$s"-*.binlog > /dev/null 2>&1
done < "$U"
