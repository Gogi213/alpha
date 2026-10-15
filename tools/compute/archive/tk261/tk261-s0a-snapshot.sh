echo "== shm sizes"; du -sk /dev/shm/* 2>/dev/null | sort -rn | head -20; ls -la --time-style=long-iso /dev/shm | head -20
echo "== df shm"; df -h /dev/shm | tail -1
echo "== fuser/lsof"; for d in /dev/shm/alpha-run /dev/shm/alpha-stand /dev/shm/tk048appr /dev/shm/tk148-dbg; do echo "-- $d"; fuser -vm $d 2>&1 | head -5; done
which lsof && lsof +D /dev/shm 2>/dev/null | head -20
echo "== mounts using shm"; findmnt -rn | grep -i shm
echo "== open maps to shm"; grep -l "/dev/shm" /proc/[0-9]*/maps 2>/dev/null | head
echo "== meminfo"; grep -E "MemTotal|MemFree|MemAvailable|^Cached|Shmem:|SwapTotal|SwapFree|SwapCached|AnonHuge|Dirty:" /proc/meminfo
echo "== swap"; swapon --show; cat /proc/swaps
echo "== vmstat"; grep -E "thp_fault_alloc|thp_fault_fallback |compact_stall|compact_fail|compact_success|pswpin|pswpout|pgmajfault|nr_shmem|allocstall" /proc/vmstat
echo "== thp"; cat /sys/kernel/mm/transparent_hugepage/enabled /sys/kernel/mm/transparent_hugepage/defrag
echo "== cpufreq"; cat /sys/devices/system/cpu/intel_pstate/no_turbo 2>&1; cat /sys/devices/system/cpu/intel_pstate/status 2>&1; cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_driver /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor /sys/devices/system/cpu/cpu0/cpufreq/scaling_min_freq /sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq 2>&1
echo "== tools"; which turbostat perf mpstat iostat filefrag fuser lsof 2>&1
echo "== sched"; for d in sda sdb; do echo $d $(cat /sys/block/$d/queue/scheduler) ra=$(cat /sys/block/$d/queue/read_ahead_kb); done
cat /sys/block/sdb/queue/iosched/low_latency 2>&1 | head -2; ls /sys/block/sdb/queue/iosched/ 2>&1
echo "== modules bfq"; lsmod | grep -i bfq; ls /lib/modules/$(uname -r)/kernel/block/ 2>&1 | head
