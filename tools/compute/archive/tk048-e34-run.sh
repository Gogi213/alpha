#!/usr/bin/env bash
# TK-048 (Судья 01:52, п.2): perf d15 -g на БОЕВОМ режиме волны: флаги обёртки alpha-e17p2flag.sh (с ALPHA_SKIP_SAME=1) + --events wide (как job3m волны), бинарник alpha-e32-sig (symtab+eh_frame, dwarf-стек).
#   done: /data/tk048/e34-d15/done; top-sym.txt (self), top-children.txt (inclusive), callers-update.txt (кто зовёт update_*depth); perf внутри юнита стенда (stand-perf.sh)
set -euo pipefail
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o BatchMode=yes); C=root@89.163.242.211
ssh "${K[@]}" $C 'mkdir -p /data/tk048/e34-d15; rm -f /data/tk048/e34-d15/done /data/tk048/e34-d15/perf.data
sed "s#-o \$R/time.txt bash \$W/run.sh#-o \$R/time.txt perf record -F 199 --call-graph dwarf,8192 -o /data/tk048/e34-d15/perf.data -- bash \$W/run.sh#; s#^cat > \$W/unit.sh#sed -i \"s| lob bounce-grid | lob bounce-grid --events wide |\" \$W/run.sh\ncat > \$W/unit.sh#" /data/tk051/stand.sh > /data/tk051/stand-perf34.sh
grep -c "perf record" /data/tk051/stand-perf34.sh; grep -c "events wide" /data/tk051/stand-perf34.sh
F="ALPHA_SKIP_SAME=1 ALPHA_SKIP_NOSIGNAL=1 ALPHA_FAST_HOLD=1 ALPHA_EVENT_STEPS=entry ALPHA_SIG_CACHE=3000000 ALPHA_APPROACH_BIN=1 ALPHA_ADMIT_CACHE=1 ALPHA_HOLDS_MEMO=1 ALPHA_ADMIT_SOA=1 ALPHA_BAND_COUNT_OFF=1 ALPHA_DIRECT_FEED=1 ALPHA_TOUCH_BIN=1"
cat > /data/tk048/e34-d15/run.sh <<EOS
#!/bin/bash
D=/data/tk048/e34-d15; cd /data/tk051
/data/benchrun.sh stand bash stand-perf34.sh alpha-e32-sig d15 TAG=e34 $F > \$D/out.txt 2>&1
perf report -i \$D/perf.data --no-children --comm alpha --sort sym --stdio -g none 2>/dev/null | grep -v "^#" | grep -v "^\$" | head -80 > \$D/top-sym.txt
perf report -i \$D/perf.data --children --comm alpha --sort sym --stdio -g none 2>/dev/null | grep -v "^#" | grep -v "^\$" | head -80 > \$D/top-children.txt
perf report -i \$D/perf.data --no-children --comm alpha --sort sym --stdio -G -S update_bid_depth,update_ask_depth 2>/dev/null | grep -v "^#" | head -120 > \$D/callers-update.txt
touch \$D/done
EOS
systemctl reset-failed tk048-e34 2>/dev/null; systemd-run --quiet --unit=tk048-e34 --collect bash /data/tk048/e34-d15/run.sh && echo запущено'
