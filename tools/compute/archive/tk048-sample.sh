#!/bin/bash
# tk048-sample.sh <файл>: раз в секунду строка t user nice sys idle iowait | sdb МБ-прочит io_ticks | sda то же | Cached_kB | единиц считается
while sleep 1; do
  echo "$(date +%s.%N) $(awk '/^cpu /{print $2,$3,$4,$5,$6}' /proc/stat) $(awk '$3=="sdb"{printf "%d %d",$6,$13}' /proc/diskstats) $(awk '$3=="sda"{printf "%d %d",$6,$13}' /proc/diskstats) $(awk '/^Cached:/{print $2}' /proc/meminfo) $(pgrep -c -f 'alpha.*bounce-grid')"
done >> "$1"
