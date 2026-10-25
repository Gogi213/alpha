#!/bin/bash
# TK-401 П-29: проба 1 монеты (R7) — AIGENSYN (EVM v3, USD-квота, gev2 173 ч)
O=/data/tk0393/tk401/ev; mkdir -p $O; rm -f $O/probe.done
/data/tk0380/npvenv/bin/python /data/tk0393/tk401-ev.py ${1:-AIGENSYN} /data/tk0393/tk401/mid/${1:-AIGENSYN}.csv.gz $O/${1:-AIGENSYN} > $O/${1:-AIGENSYN}.log 2>&1
echo rc=$? >> $O/${1:-AIGENSYN}.log; touch $O/probe.done
