#!/usr/bin/env bash
# TK-401 П-29: бой — все монеты mid (67) по событиям; фьюч gev2 (tk401+tk402 через симлинки) + спот по сделкам. TRUST первым (проба по книге R7).
#   python3 /data/sched/alsched.py submit --cls prod --name tk401-ev --cores 15 --mem 40 --max-runtime 1h --cwd /data/tk0393/tk401/ev -- bash /data/tk0393/tk401/ev/battle.sh
O=/data/tk0393/tk401/ev; G=/data/tk0393/tk401/gev2all; PY=/data/tk0380/npvenv/bin/python
rm -f $O/battle.done $O/trustbook.done; mkdir -p $G
for d in /data/tk0402/gev2/* /data/tk0393/tk401/gev2/*; do ln -sfn $d $G/$(basename $d); done
export GEV=$G
$PY /data/tk0393/tk401-ev.py TRUST /data/tk0393/tk401/mid/TRUST.csv.gz $O/TRUST > $O/TRUST.log 2>&1; echo rc=$? >> $O/TRUST.log; touch $O/trustbook.done
ls /data/tk0393/tk401/mid | sed 's/\.csv\.gz$//' | grep -v '^TRUST$' | xargs -P15 -I{} sh -c "$PY /data/tk0393/tk401-ev.py {} /data/tk0393/tk401/mid/{}.csv.gz $O/{} > $O/{}.log 2>&1; echo rc=\$? >> $O/{}.log"
echo "rc=$? монет $(ls /data/tk0393/tk401/mid | wc -l) curve $(ls $O/*-curve.csv | wc -l) $(date +%T)" > $O/battle.done
