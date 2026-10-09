#!/usr/bin/env bash
# TK-048 К-4д: ABBA показал индекс ×2,3 МЕДЛЕННЕЕ (386 против 167 с, diff 0) — perf d15 (плоский, self) alpha-e36-idx + HOLD_INDEX=1, боевые флаги + wide.
#   done: /data/tk048/e38-d15/done; top-sym.txt
set -euo pipefail
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o BatchMode=yes); C=root@89.163.242.211
ssh "${K[@]}" $C 'mkdir -p /data/tk048/e38-d15; rm -f /data/tk048/e38-d15/done /data/tk048/e38-d15/perf.data
sed "s#-o \$R/time.txt bash \$W/run.sh#-o \$R/time.txt perf record -F 199 -o /data/tk048/e38-d15/perf.data -- bash \$W/run.sh#; s#^cat > \$W/unit.sh#sed -i \"s| lob bounce-grid | lob bounce-grid --events wide |\" \$W/run.sh\ncat > \$W/unit.sh#" /data/tk051/stand.sh > /data/tk051/stand-perf34.sh
grep -c "perf record" /data/tk051/stand-perf34.sh; grep -c "events wide" /data/tk051/stand-perf34.sh
F="ALPHA_SKIP_SAME=1 ALPHA_SKIP_NOSIGNAL=1 ALPHA_FAST_HOLD=1 ALPHA_EVENT_STEPS=entry ALPHA_SIG_CACHE=3000000 ALPHA_APPROACH_BIN=1 ALPHA_ADMIT_CACHE=1 ALPHA_HOLDS_MEMO=1 ALPHA_ADMIT_SOA=1 ALPHA_BAND_COUNT_OFF=1 ALPHA_DIRECT_FEED=1 ALPHA_TOUCH_BIN=1 ALPHA_HOLD_INDEX=1"
cat > /data/tk048/e38-d15/run.sh <<EOS
#!/bin/bash
D=/data/tk048/e38-d15; cd /data/tk051
/data/benchrun.sh stand bash stand-perf34.sh alpha-e36-idx d15 TAG=e38 $F > \$D/out.txt 2>&1
perf report -i \$D/perf.data --no-children --comm alpha --sort sym --stdio -g none 2>/dev/null | grep -v "^#" | grep -v "^\$" | head -80 > \$D/top-sym.txt
touch \$D/done
EOS
systemctl reset-failed tk048-e38 2>/dev/null; systemd-run --quiet --unit=tk048-e38 --collect bash /data/tk048/e38-d15/run.sh && echo запущено'
