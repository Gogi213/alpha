#!/bin/bash
# TK-261 Ш2.1б: BFQ (low_latency=0) против mq-deadline НА БАЗЕ PREFETCH=0 (итог Ш2.5). B1 B2 = bfqp1 bfqp2, контроль A = pfd; A1/A2 = pfb1/pfb2/pfc (PREFETCH=0, mq-deadline, уже сняты).
# Подача: systemd-run --unit tk261-abba-bfqp --collect python3 /data/sched/alsched.py wave --max-runtime 40m bash /data/tk0261/tk261-abba-bfq-pf0.sh
D=/data/tk0261
bash $D/tk261-wave.sh bfqp1 $D/tk261-bfq-on.sh $D/tk261-bfq-off.sh 0
bash $D/tk261-wave.sh bfqp2 $D/tk261-bfq-on.sh $D/tk261-bfq-off.sh 0
for d in sda sdb; do echo "before pfd $d $(cat /sys/block/$d/queue/scheduler)"; done
bash $D/tk261-wave.sh pfd "" "" 0
date -Is > $D/abba-bfqp.done
