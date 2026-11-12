#!/bin/bash
# TK-385 y11 (Судья, reviews/tk385-yorsh-y10): база lag2 + задержка 1 с + пессимистичная очередь (PESS=1, только сквозь-принт); нули MA1/MA5 и сдвиг DEX +3 ч с теми же условиями; оптимистичная (PESS=0) справкой. Q,ZEC,SUE,LAPTOP,BTR,DGAI,CAKE; k=0,2/0,5/1 %, T=300, sym, med. 15 воркеров.
cd /data/tk0380; mkdir -p tk385/y11
C="Q ZEC SUE LAPTOP BTR DGAI CAKE"
for c in $C; do for sc in pbase:0:0:1 pma1:1:0:1 pma5:5:0:1 psh3:0:3:1 obase:0:0:0; do for ki in 0 2 4; do echo "$c $sc $ki"; done; done; done > tk385/y11-jobs.txt
xargs -P 15 -L 1 bash -c 'IFS=: read n ma sh p <<< "$1"; NULL_MIN=$ma SHIFT_H=$sh LAG_S=2 DELAY_S=1 PESS=$p KI=$2 npvenv/bin/python tk385-yorsh9.py $0 $([ $0 = LAPTOP ] && echo base || echo bsc) med sym tk385/y11/$0-$n-$2.csv > tk385/y11/$0-$n-$2.log 2>&1' < tk385/y11-jobs.txt
touch tk385/y11.done
