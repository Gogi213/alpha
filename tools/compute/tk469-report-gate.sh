#!/bin/bash
# TK-469 гейт report: старая (умолчание) и TK469_REPORT_FAST=1 на одной маске, 1 ядро (taskset); время и cmp.
# MASK='1000*-60.csv.gz' D=/data/tk0461/d2b-out-bp3 R=/data/tk0469/rgate bash tk469-report-gate.sh
D=${D:-/data/tk0461/d2b-out-bp3}; R=${R:-/data/tk0469/rgate}; MASK=${MASK:-1000*-60.csv.gz}; PY=/data/tk421/venv/bin/python
S=$(dirname "$0"); mkdir -p $R; rm -f $R/DONE
cd $S
/usr/bin/time -f "old %e с, пик RSS %M КБ" -o $R/old.time taskset -c ${CPU:-15} $PY tk461_d2b_report.py $D $R/o_cells.csv $R/o_years.csv 1 "$MASK" > $R/old.out 2>&1
TK469_REPORT_FAST=1 /usr/bin/time -f "fast %e с, пик RSS %M КБ" -o $R/new.time taskset -c ${CPU:-15} $PY tk461_d2b_report.py $D $R/n_cells.csv $R/n_years.csv 1 "$MASK" > $R/new.out 2>&1
$PY tk469_report_gate.py $R/o_cells.csv $R/n_cells.csv $R/o_years.csv $R/n_years.csv > $R/gate.out 2>&1; echo "rc $?" >> $R/gate.out
touch $R/DONE
