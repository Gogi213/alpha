#!/bin/bash
# TK-458 G1.1: только агрегатор (события уже в ev5). Метка DONE6
PY=/data/tk421/venv/bin/python
$PY -I /data/tk0451/bp_g1_agg2.py /data/tk458/ev5 /data/tk458/g1 > /data/tk458/agg6.log 2>&1
touch /data/tk458/DONE6
