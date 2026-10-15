#!/bin/bash
# TK-261 Ш2.4: read_ahead_kb 512 против 65536 (исходное) НА БАЗЕ PREFETCH=0 (итог Ш2.5). Порядок: ra1 ra2 (B) и контроль pfc (A); A1/A2 = pfb1/pfb2 (PREFETCH=0, ra 65536, уже сняты).
# Подача: systemd-run --unit tk261-abba-ra --collect python3 /data/sched/alsched.py wave --max-runtime 40m bash /data/tk0261/tk261-abba-ra.sh
D=/data/tk0261
bash $D/tk261-wave.sh ra1 $D/tk261-ra-on.sh $D/tk261-ra-off.sh 0
bash $D/tk261-wave.sh ra2 $D/tk261-ra-on.sh $D/tk261-ra-off.sh 0
for d in sda sdb; do echo "before pfc $d ra=$(cat /sys/block/$d/queue/read_ahead_kb)"; done
bash $D/tk261-wave.sh pfc "" "" 0
date -Is > $D/abba-ra.done
