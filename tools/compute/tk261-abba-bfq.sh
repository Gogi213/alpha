#!/bin/bash
# TK-261 Ш2.1: ABBA BFQ против mq-deadline. A1 = b0 (уже снята), здесь B1 B2 A2 в одном окне alsched wave.
# Подача: systemd-run --unit tk261-abba-bfq --collect python3 /data/sched/alsched.py wave --max-runtime 50m bash /data/tk0261/tk261-abba-bfq.sh
D=/data/tk0261
for d in sda sdb; do echo "start $d $(cat /sys/block/$d/queue/scheduler)"; done
bash $D/tk261-wave.sh bfq1 $D/tk261-bfq-on.sh $D/tk261-bfq-off.sh
bash $D/tk261-wave.sh bfq2 $D/tk261-bfq-on.sh $D/tk261-bfq-off.sh
for d in sda sdb; do echo "before A2 $d $(cat /sys/block/$d/queue/scheduler)"; done
bash $D/tk261-wave.sh a2
date -Is > $D/abba-bfq.done
