#!/bin/bash
# benchrun.sh <wave|stand> <команда…>: замок замеров с приоритетом волны (/data/benchrun.sh -> /data/tk048/benchrun.sh).
# stand — главный замок общий (стенды идут вместе); wave — эксклюзивно, держа ворота, и на время волны ЗАМОРАЖИВАЕТ чужие счётные юниты
# (tk0*/t4*/t5*/run-*, кроме tk048-*, alpha-*); thaw — по выходу. Список с ЦП до: $BENCH_FROZEN (читает tk048-tail.sh).
cls=$1; shift
case $cls in wave|stand) ;; *) echo "benchrun.sh: класс wave|stand" >&2; exit 2;; esac
exec 8>/data/tk-bench.gate 9>/data/tk-bench.lock
flock -x 8
if [ "$cls" = wave ]; then flock -x 9; else flock -s 9; fi
flock -u 8; exec 8>&-
if [ "$cls" = wave ]; then
  own=$(sed 's#.*/##' /proc/self/cgroup)
  export BENCH_FROZEN=/data/tk048/frozen-$$.txt; : > "$BENCH_FROZEN"
  thaw_all() { while read -r u _; do systemctl thaw "$u" </dev/null 2>/dev/null; done < "$BENCH_FROZEN"; }
  trap 'thaw_all' EXIT; trap 'exit 143' TERM INT
  for u in $(systemctl list-units --type=service --state=active --no-legend --plain 'tk0*' 't4*' 't5*' 'run-*' | awk '{print $1}'); do
    case $u in tk048-*|alpha-*|"$own") continue;; esac
    c=$(systemctl show -p CPUUsageNSec --value "$u")
    systemctl freeze "$u" </dev/null 2>/dev/null && echo "$u $c" >> "$BENCH_FROZEN"
  done
fi
"$@"; exit $?
