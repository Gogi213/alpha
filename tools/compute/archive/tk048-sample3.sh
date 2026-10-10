#!/bin/bash
# tk048-sample3.sh <файл>: как sample.sh + по sdb и sda: чтений, секторов чтения, записей, секторов записи, io_ticks (раз в секунду)
while sleep 1; do
  echo "$(date +%s.%N) $(awk '$3=="sdb"||$3=="sda"{printf "%s %d %d %d %d %d ",$3,$4,$6,$8,$10,$13}' /proc/diskstats)"
done >> "$1"
