#!/bin/bash
# TK-278 п.4: сито F1 по всем 50 гипотезам — python над готовыми closes с монетой (без движка). Выход /data/tk0278/sieve/sieve-all.csv, rc.txt.
D=/data/tk0278/sieve; cd $D || exit 2; rm -f rc.txt
python3 tk278-sieve.py km.json tk237-grid-coverage-2026-10-13.csv cellhash-map-2026-10-11.csv sieve-all.csv > sieve.log 2>&1; echo $? > rc.txt
