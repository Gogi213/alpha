#!/bin/bash
# TK-385 y9: контроли Судьи на Q,ZEC: нуль (сдвиг DEX +1,+3,+6,-3 ч), лаг 2/5/10 с, лот=медиана принта, база; k=0,2/0,5/1 %, T=300, sym. 54 задания на 15 воркерах.
cd /data/tk0380; mkdir -p tk385/y9
for c in Q ZEC; do for sc in base:0:0:0 sh1:1:0:0 sh3:3:0:0 sh6:6:0:0 shm3:-3:0:0 lag2:0:2:0 lag5:0:5:0 lag10:0:10:0 lot1:0:0:1; do for ki in 0 2 4; do echo "$c $sc $ki"; done; done; done > tk385/y9-jobs.txt
xargs -P 15 -L 1 bash -c 'IFS=: read n sh lag lot <<< "$1"; SHIFT_H=$sh LAG_S=$lag LOTX=$lot KI=$2 npvenv/bin/python tk385-yorsh7.py $0 bsc med sym tk385/y9/$0-$n-$2.csv > tk385/y9/$0-$n-$2.log 2>&1' < tk385/y9-jobs.txt
touch tk385/y9.done
