#!/usr/bin/env bash
# TK-048 К-7 (Судья 09:26): ЦП боя — perf -g (dwarf) на d15 с БОЕВЫМИ флагами ВКЛЮЧАЯ ALPHA_SKIP_SAME=1, бинарник alpha-e21-dbg (debug=1).
#   done: /data/tk048/e50-d15/done; top-sym.txt (self), children.txt, top-line.txt (self по строкам), callers-*.txt (кто зовёт горячие символы)
set -euo pipefail
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o BatchMode=yes); C=root@89.163.242.211
ssh "${K[@]}" $C 'mkdir -p /data/tk048/e50-d15; rm -f /data/tk048/e50-d15/done /data/tk048/e50-d15/perf.data
sed "s#-o \$R/time.txt bash \$W/run.sh#-o \$R/time.txt perf record -F 299 --call-graph dwarf,16384 -o /data/tk048/e50-d15/perf.data -- bash \$W/run.sh#" /data/tk051/stand.sh > /data/tk051/stand-perf50.sh
grep -c "perf record" /data/tk051/stand-perf50.sh
cat > /data/tk048/e50-d15/run.sh <<'"'"'EOS'"'"'
#!/bin/bash
D=/data/tk048/e50-d15; cd /data/tk051
/data/benchrun.sh stand bash stand-perf50.sh alpha-e21-dbg d15 ALPHA_SKIP_SAME=1 ALPHA_SKIP_NOSIGNAL=1 ALPHA_FAST_HOLD=1 ALPHA_EVENT_STEPS=entry ALPHA_SIG_CACHE=3000000 ALPHA_APPROACH_BIN=1 ALPHA_ADMIT_CACHE=1 ALPHA_HOLDS_MEMO=1 ALPHA_ADMIT_SOA=1 ALPHA_BAND_COUNT_OFF=1 ALPHA_DIRECT_FEED=1 ALPHA_TOUCH_BIN=1 > $D/out.txt 2>&1
P="perf report -i $D/perf.data --comm alpha --stdio --percent-limit 0.3"
$P --no-children -g none --sort sym 2>/dev/null | grep -v "^#" | grep -v "^$" | head -70 > $D/top-sym.txt
$P --children -g none --sort sym 2>/dev/null | grep -v "^#" | grep -v "^$" | head -90 > $D/children.txt
$P --no-children -g none --sort srcline --full-source-path 2>/dev/null | grep -v "^#" | grep -v "^$" | head -60 > $D/top-line.txt
for s in update_bid_depth update_ask_depth decide_exit windowed_with; do $P --no-children -G --symbols $s --max-stack 6 --sort sym 2>/dev/null | grep -v "^#" | grep -v "^$" | head -60 > $D/callers-$s.txt; done
touch $D/done
EOS
systemctl reset-failed tk048-e50 2>/dev/null; systemd-run --quiet --unit=tk048-e50 --collect bash /data/tk048/e50-d15/run.sh && echo запущено'
