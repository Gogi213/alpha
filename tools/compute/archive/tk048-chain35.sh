#!/bin/bash
# chain35: две холодные волны q15 wide на b14 (G=8 P=15): без читателя вперёд и с PREFETCH=2 (grp12); сравнение с q15b13s2 262,5 с и тёплым 216 с
rm -f /data/tk048/chain35.done
D15=$(seq -s, -f "2026-01-%02g" 1 15)
for v in n:0 p:2; do
  lab=${v%%:*}; pf=${v##*:}
  systemd-run --wait --collect --unit tk048-q15b14$lab -p CPUQuota=1500% --setenv=PREFETCH=$pf --setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 /data/tk052/benchrun2.sh wave bash /data/tk048/tk048-orch-grp12.sh q15b14$lab alpha-b14flag $D15 8 15 > /dev/null 2>&1
done
touch /data/tk048/chain35.done
