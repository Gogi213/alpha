#!/bin/bash
# TK-261 Ш2.5: ABBA прогреватель суток (PREFETCH=5, умолч.) против PREFETCH=0 (выкл.). A B B A, одно окно alsched wave, ОС без правок.
# Подача: systemd-run --unit tk261-abba-pf --collect python3 /data/sched/alsched.py wave --max-runtime 60m bash /data/tk0261/tk261-abba-prefetch.sh
D=/data/tk0261
bash $D/tk261-wave.sh pfa1 "" "" 5
bash $D/tk261-wave.sh pfb1 "" "" 0
bash $D/tk261-wave.sh pfb2 "" "" 0
bash $D/tk261-wave.sh pfa2 "" "" 5
date -Is > $D/abba-pf.done
