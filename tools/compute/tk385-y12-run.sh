#!/bin/bash
# TK-385 y12 (Судья, reviews/tk385-yorsh-y11): очередь в хвосте уровня (PESS=2: котировка внутри спреда -> лучшая цена своей стороны, L2-очередь), задержка 1 с, lag 2 с; база + нули MA1/MA5/сдвиг +3 ч с теми же условиями; adv60/adv300. Q,ZEC,SUE,LAPTOP,BTR,DGAI,CAKE; k=0,2/0,5/1 %, T=300, sym, med. 15 воркеров.
cd /data/tk0380; mkdir -p tk385/y12
C="Q ZEC SUE LAPTOP BTR DGAI CAKE"
for c in $C; do for sc in qbase:0:0 qma1:1:0 qma5:5:0 qsh3:0:3; do for ki in 0 2 4; do echo "$c $sc $ki"; done; done; done > tk385/y12-jobs.txt
xargs -P 15 -L 1 bash -c 'IFS=: read n ma sh <<< "$1"; NULL_MIN=$ma SHIFT_H=$sh LAG_S=2 DELAY_S=1 PESS=2 KI=$2 npvenv/bin/python tk385-yorsh10.py $0 $([ $0 = LAPTOP ] && echo base || echo bsc) med sym tk385/y12/$0-$n-$2.csv > tk385/y12/$0-$n-$2.log 2>&1' < tk385/y12-jobs.txt
touch tk385/y12.done
