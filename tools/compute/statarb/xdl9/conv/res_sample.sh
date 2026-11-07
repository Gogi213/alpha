#!/bin/bash
# TK-493 (GW 07.11): замер ресурсов Deck раз в SEC секунд до остановки (kill). Колонки: время UTC, свободно диска /home (МиБ), MemAvailable (МиБ), суммарный RSS процессов PATTERN (МиБ;
# общие страницы fork считаются повторно - оценка сверху), число таких процессов, loadavg за 1 мин.
# Использование:  res_sample.sh OUT.csv [SEC=5] [PATTERN='conv_window.py run'] &   ...  kill %1
OUT=$1
SEC=${2:-5}
PAT=${3:-conv_window.py run}
echo "time_utc,disk_free_mib,mem_avail_mib,rss_sum_mib,nproc,load1" > "$OUT"
while true; do
  t=$(date -u +%FT%TZ)
  d=$(df -B1M --output=avail /home | tail -1 | tr -d ' ')
  m=$(awk '/MemAvailable/ {printf "%d", $2/1024}' /proc/meminfo)
  r=$(ps -eo rss,args | awk -v p="$PAT" 'index($0, p) && !/awk/ && !/res_sample/ {s += $1; n++} END {printf "%d,%d", s/1024, n}')
  l=$(cut -d' ' -f1 /proc/loadavg)
  echo "$t,$d,$m,$r,$l" >> "$OUT"
  sleep "$SEC"
done
