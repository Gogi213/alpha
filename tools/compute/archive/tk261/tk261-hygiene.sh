#!/bin/bash
# TK-261 Ш1: гигиена — старые папки /dev/shm удалить, swapoff/swapon; снимки до/после
O=/data/tk0261; exec > $O/h1.out 2>&1
snap() { echo "--- $1 $(date -Is)"; grep -E "^(MemFree|MemAvailable|Cached|Shmem|SwapCached|SwapFree):" /proc/meminfo; grep -E "^(pswpin|pswpout|nr_shmem) " /proc/vmstat; swapon --show --noheadings; }
snap before
ls -la --time-style=long-iso /dev/shm > $O/shm-manifest.txt
du -sk /dev/shm/* >> $O/shm-manifest.txt
ls -R /dev/shm/alpha-run /dev/shm/tk148-dbg >> $O/shm-manifest.txt
for d in alpha-run alpha-stand tk048appr tk148-dbg; do rm -rf /dev/shm/$d; done
ls -la /dev/shm
snap after_rm
s=$(date +%s.%N); swapoff -a; echo "swapoff rc=$? $(echo "$(date +%s.%N) - $s" | bc) s"
snap swapoff
grep -c swap /etc/fstab
swapon -a; echo "swapon -a rc=$?"
swapon --show --noheadings | grep -q sda3 || { swapon /dev/sda3; echo "swapon sda3 rc=$?"; }
snap after
date -Is > $O/h1.done
