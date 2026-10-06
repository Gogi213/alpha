#!/bin/bash
# TK-071: внутри окна замера (юнит демона): env вызывающего, снимок реестра TK-068, учёт дисков TK-052, команда. benchrun-inner.sh <envfile> <cls> <команда…>
E=$1; cls=$2; shift 2
set +u; . "$E"
[ -n "$ALSCHED_SLICE" ] && export PATH=/data/sched/shim:$PATH     # env вызывающего затёр PATH демона — вернуть шим вложенных systemd-run
own_cg=$(sed s#^0::## /proc/self/cgroup)
export BENCH_IOSTATE=/data/tk048/ioacct-$$.json
REGM=$(python3 /data/registry/snap.py begin "$cls" "$@" 2>/dev/null) || REGM=""
python3 /data/tk052/io-acct.py run $BENCH_IOSTATE "$own_cg" </dev/null >/dev/null 2>&1 & IOPID=$!
"$@"; rc=$?
kill $IOPID 2>/dev/null; wait $IOPID 2>/dev/null
python3 /data/tk052/io-acct.py report $BENCH_IOSTATE > /data/tk048/ioacct-$$.txt 2>&1
[ -n "$REGM" ] && python3 /data/registry/snap.py end "$REGM" "$rc" 2>/dev/null
exit $rc
