#!/bin/bash
# TK-385 y13 (Судья, reviews/tk385-yorsh-y12): без заглядывания — посекундные BBO/DEX (yorsh11), LOTX=1, тариф 1.3/3.25 бп (VIP0 2/5 ×0.65, CEO 23:55) + колонка VIP0; Q,ZEC,SUE,LAPTOP,BTR,DGAI,CAKE: база + нули MA1/MA5/+3ч; контроли Q: LAG 0 / LAG 10 / DEX из будущего (−10 с). k=0,2/0,5/1 %, T=300, sym, med, PESS=2, DELAY 1. 15 воркеров.
cd /data/tk0380; mkdir -p tk385/y13
C="Q ZEC SUE LAPTOP BTR DGAI CAKE"
for c in $C; do for sc in qbase:0:0:2 qma1:1:0:2 qma5:5:0:2 qsh3:0:10800:2; do for ki in 0 2 4; do echo "$c $sc $ki"; done; done; done > tk385/y13-jobs.txt
for sc in qlag0:0:0:0 qlag10:0:0:10 qfut:0:-10:2; do for ki in 0 2 4; do echo "Q $sc $ki"; done; done >> tk385/y13-jobs.txt
xargs -P 15 -L 1 bash -c 'IFS=: read n ma ss lag <<< "$1"; NULL_MIN=$ma SHIFT_S=$ss LAG_S=$lag DELAY_S=1 PESS=2 LOTX=1 KI=$2 npvenv/bin/python tk385-yorsh11.py $0 $([ $0 = LAPTOP ] && echo base || echo bsc) med sym tk385/y13/$0-$n-$2.csv > tk385/y13/$0-$n-$2.log 2>&1' < tk385/y13-jobs.txt
touch tk385/y13.done
