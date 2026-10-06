#!/bin/bash
# [tk052: benchrun2 = benchrun + учёт дисков io-acct.py: BENCH_IOSTATE, отчёт /data/tk048/ioacct-<pid>.txt]
# benchrun.sh <wave|stand> <команда…>: замок замеров с приоритетом волны (/data/benchrun.sh -> /data/tk048/benchrun.sh).
# stand — главный замок общий (стенды идут вместе); wave — эксклюзивно, держа ворота, и на время волны ЗАМОРАЖИВАЕТ чужие счётные юниты
# (tk0*/t4*/t5*/run-*, кроме tk048-*, alpha-*); thaw — по выходу. Список с ЦП до: $BENCH_FROZEN (читает tk048-tail.sh).
cls=$1; shift
case $cls in wave|stand) ;; *) echo "benchrun.sh: класс wave|stand" >&2; exit 2;; esac
exec 8>/data/tk-bench.gate 9>/data/tk-bench.lock
flock -x 8
if [ "$cls" = wave ]; then flock -x 9; else flock -s 9; fi
flock -u 8; exec 8>&-
own_cg=$(sed s#^0::## /proc/self/cgroup); own_u=${own_cg##*/}
case $own_u in *.service) systemctl set-property --runtime "$own_u" IOAccounting=yes </dev/null 2>/dev/null;; esac
export BENCH_IOSTATE=/data/tk048/ioacct-$$.json
REGM=$(python3 /data/registry/snap.py begin "$cls" "$@" 2>/dev/null) || REGM=""   # TK-068: снимок конфига прогона  # до учёта ввода-вывода — не попадает в байты волны
python3 /data/tk052/io-acct.py run $BENCH_IOSTATE "$own_cg" </dev/null >/dev/null 2>&1 & IOPID=$!
if [ "$cls" = wave ]; then
  own=$(sed 's#.*/##' /proc/self/cgroup)
  export BENCH_FROZEN=/data/tk048/frozen-$$.txt; : > "$BENCH_FROZEN"
  thaw_all() { while read -r u _; do systemctl thaw "$u" </dev/null 2>/dev/null; done < "$BENCH_FROZEN"; }
  trap 'thaw_all' EXIT; trap 'exit 143' TERM INT
  for u in $(systemctl list-units --type=service --state=active --no-legend --plain 'tk0*' 't4*' 't5*' 'run-*' | awk '{print $1}'); do
    case $u in alpha-*|"$own") continue;; esac
    c=$(systemctl show -p CPUUsageNSec --value "$u")
    systemctl freeze "$u" </dev/null 2>/dev/null && echo "$u $c" >> "$BENCH_FROZEN"
  done
fi
"$@"; rc=$?; kill $IOPID 2>/dev/null; wait $IOPID 2>/dev/null; python3 /data/tk052/io-acct.py report $BENCH_IOSTATE > /data/tk048/ioacct-$$.txt 2>&1
[ -n "$REGM" ] && python3 /data/registry/snap.py end "$REGM" "$rc" 2>/dev/null   # TK-068
exit $rc
