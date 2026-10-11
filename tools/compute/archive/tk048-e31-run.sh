#!/usr/bin/env bash
# TK-048 (Судья 00:40, п.1): доля ЦП в бою — perf d15 на БОЕВЫХ флагах (alpha-e17p2flag.sh + TOUCH_BIN), бинарник alpha-e21-dbg (debug=1, код e20: FAST_HOLD/HoldTracker есть)
#   done: /data/tk048/e31-d15/done; top-sym.txt (--comm alpha, по символам); perf внутри юнита стенда (stand-perf.sh), не снаружи benchrun
set -euo pipefail
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o BatchMode=yes); C=root@89.163.242.211
ssh "${K[@]}" $C 'mkdir -p /data/tk048/e31-d15; rm -f /data/tk048/e31-d15/done /data/tk048/e31-d15/perf.data
sed "s#-o \$R/time.txt bash \$W/run.sh#-o \$R/time.txt perf record -F 499 -o /data/tk048/e31-d15/perf.data -- bash \$W/run.sh#" /data/tk051/stand.sh > /data/tk051/stand-perf.sh
grep -c "perf record" /data/tk051/stand-perf.sh
F="ALPHA_SKIP_NOSIGNAL=1 ALPHA_FAST_HOLD=1 ALPHA_EVENT_STEPS=entry ALPHA_SIG_CACHE=3000000 ALPHA_APPROACH_BIN=1 ALPHA_ADMIT_CACHE=1 ALPHA_HOLDS_MEMO=1 ALPHA_ADMIT_SOA=1 ALPHA_BAND_COUNT_OFF=1 ALPHA_DIRECT_FEED=1 ALPHA_TOUCH_BIN=1"
cat > /data/tk048/e31-d15/run.sh <<EOS
#!/bin/bash
D=/data/tk048/e31-d15; cd /data/tk051
/data/benchrun.sh stand bash stand-perf.sh alpha-e21-dbg d15 $F > \$D/out.txt 2>&1
perf report -i \$D/perf.data --no-children --comm alpha --sort sym --stdio 2>/dev/null | grep -v "^#" | grep -v "^\$" | head -80 > \$D/top-sym.txt
touch \$D/done
EOS
systemctl reset-failed tk048-e31 2>/dev/null; systemd-run --quiet --unit=tk048-e31 --collect bash /data/tk048/e31-d15/run.sh && echo запущено'
