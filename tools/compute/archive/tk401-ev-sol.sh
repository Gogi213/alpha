#!/usr/bin/env bash
# TK-401 П-29, возврат Судьи 26.10: бой по 31 Solana-монете (mid-sol, swaps3 дедуп); фьюч gev2all (5 с книгой), спот по сделкам. Выход ev-sol/.
#   python3 /data/sched/alsched.py submit --cls prod --name tk401-evsol --cores 15 --mem 40 --max-runtime 30m --est 5m --cwd /data/tk0393/tk401/ev-sol -- bash /data/tk0393/tk401/ev-sol/battle-sol.sh
O=/data/tk0393/tk401/ev-sol; PY=/data/tk0380/npvenv/bin/python; mkdir -p $O; rm -f $O/sol.done
export GEV=/data/tk0393/tk401/gev2all
ls /data/tk0393/tk401/mid-sol | sed 's/\.csv\.gz$//' | xargs -P15 -I{} sh -c "$PY /data/tk0393/tk401-ev.py {} /data/tk0393/tk401/mid-sol/{}.csv.gz $O/{} > $O/{}.log 2>&1; echo rc=\$? >> $O/{}.log"
echo "монет $(ls /data/tk0393/tk401/mid-sol | wc -l) curve $(ls $O/*-curve.csv 2>/dev/null | wc -l) $(date +%T)" > $O/sol.done
# агрегат R4–R6 (1344, пред-ход, сутки, охват): evm + sol + вместе
/data/tk0380/npvenv/bin/python /data/tk0393/tk401-ev-agg.py /data/tk0393/tk401/ev-sol/final evm=/data/tk0393/tk401/ev sol=$O > $O/final.txt 2>&1; echo "agg rc=$?" >> $O/sol.done
