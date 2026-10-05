#!/bin/bash
# tk048-tail.sh snap | tail <R> <юнит>: снимок cgroup-ЦП (мкс) / в метрики: замороженные юниты (ЦП до/после) и «кто ел ЦП» при помехе.
snap() { for f in /sys/fs/cgroup/system.slice/*/cpu.stat /sys/fs/cgroup/user.slice/*/cpu.stat /sys/fs/cgroup/user.slice/*/*/cpu.stat; do [ -e "$f" ] && echo "$(echo $f | sed 's#/sys/fs/cgroup/##; s#/cpu.stat##') $(awk '/usage_usec/{print $2}' $f)"; done | sort; }
if [ "$1" = snap ]; then snap; exit 0; fi
R=$2; U=$3; snap > $R/cg1.txt; M=$R/metrics.txt
if [ -s "${BENCH_FROZEN:-/nonexistent}" ]; then
  echo "frozen_units $(wc -l < $BENCH_FROZEN)" >> $M
  while read -r u c0; do echo "frozen $u cpu_before_s $((c0/1000000)) cpu_after_s $(( $(systemctl show -p CPUUsageNSec --value $u </dev/null)/1000000 ))" >> $M; done < $BENCH_FROZEN
else echo "frozen_units 0" >> $M; fi
if grep -q INVALID_INTERFERENCE $M; then
  join $R/cg0.txt $R/cg1.txt | awk '{d=($3-$2)/1e6; if (d>1) printf "%.0f %s\n", d, $1}' | sort -rn | head -6 | sed 's/^/who_ate_cpu_s /' >> $M
fi
