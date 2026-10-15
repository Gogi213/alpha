echo "== procs with fd/cwd/maps into shm"; for p in /proc/[0-9]*; do for f in $p/fd/* $p/cwd; do l=$(readlink $f 2>/dev/null); case "$l" in /dev/shm/*) echo "$p $(cat $p/comm) $l";; esac; done; done 2>/dev/null | sort -u | head
echo "== env refs in unit files / scripts on calc"; grep -rIl -E "/dev/shm/(alpha-run|alpha-stand|tk048appr|tk148-dbg)" /etc/systemd/system /opt/alpha-launch /etc/alpha-launch /data/sched/*.py /data/sched/*.sh /data/*.sh /usr/local/bin 2>/dev/null | head -20
echo "== shm subdirs"; for d in alpha-run alpha-stand tk048appr tk148-dbg; do echo "-- $d"; ls -la --time-style=long-iso /dev/shm/$d | head -12; done
echo "== alpha-stand sizes"; du -sk /dev/shm/alpha-stand/* 2>/dev/null | sort -rn | head
echo "== cpuinfo MHz"; grep MHz /proc/cpuinfo | awk '{s+=$4;n++} END{print n, s/n}'
echo "== day-cells BIN"; grep -n -E "BIN=|alpha-tk190b|BIN:-" /data/sched/day-cells.sh /data/*/day-cells.sh /opt/alpha-compute/day-cells.sh 2>/dev/null | head
ls /data/sched/ | grep -i -E "day-cells|launch" | head
