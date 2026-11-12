#!/bin/bash
# TK-385 y10: контроли Судьи — база lag 2 с, нуль «скользящая средняя Gate» 1/5/30 мин, очередь ×2, задержка ордера 1/2 с. Q,ZEC (кэш есть), затем SUE,LAPTOP,BTR,DGAI,CAKE (кэш + base + ma5). k=0,2/0,5/1 %, T=300, sym, med. 15 воркеров.
cd /data/tk0380; mkdir -p tk385/y10
for c in Q ZEC; do for sc in base:0:0:1:0 ma1:1:0:1:0 ma5:5:0:1:0 ma30:30:0:1:0 q2:0:0:2:0 d1:0:0:1:1 d2:0:0:1:2; do for ki in 0 2 4; do echo "$c $sc $ki"; done; done; done > tk385/y10-jobs.txt
run() { xargs -P 15 -L 1 bash -c 'IFS=: read n ma sh q d <<< "$1"; NULL_MIN=$ma LAG_S=2 QMULT=$q DELAY_S=$d KI=$2 npvenv/bin/python tk385-yorsh8.py $0 $([ $0 = LAPTOP ] && echo base || echo bsc) med sym tk385/y10/$0-$n-$2.csv > tk385/y10/$0-$n-$2.log 2>&1'; }
run < tk385/y10-jobs.txt
for c in SUE LAPTOP BTR DGAI CAKE; do [ -f tk385/cache/$c/DONE ] || npvenv/bin/python tk385-prep6.py $c 15 > tk385/y10/prep-$c.log 2>&1; done
for c in SUE LAPTOP BTR DGAI CAKE; do for sc in base:0:0:1:0 ma5:5:0:1:0; do for ki in 0 2 4; do echo "$c $sc $ki"; done; done; done > tk385/y10-jobs2.txt
run < tk385/y10-jobs2.txt
touch tk385/y10.done
