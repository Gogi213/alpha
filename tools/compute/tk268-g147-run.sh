#!/bin/bash
# TK-268 Г-147: одна клетка signal=touch (--signal touch, кэш касаний D20) x 273 суток янв-сен; выход /data/tk0268/g147/out/run-<мес>-<сутки>/out/<сутки>/p05-a45-u10k/{rounds,forms,signals}.csv
export PLAN=g147 P=/data/tk0268/g147/plan O=/data/tk0268/g147/out BIN=/opt/alpha-compute/bin/alpha-tk231-pgo GT=1 TP=1
S=/data/tk0231; B=/data/tk0115/delta/b1-symdays.csv; D=/data/tk0268/g147/days.txt
if [ ! -s $D ]; then for m in jan feb mar apr may jun jul aug sep; do for d in $(bash $S/day-order.sh $B $m); do echo "$m $d"; done; done > $D.tmp && mv $D.tmp $D; fi
wc -l $D
xargs -P 6 -L1 bash -c 'bash /data/tk0268/g147/day-g147.sh "run-$1-$2" "$1" "$2"' _ < $D
echo rc=$?
