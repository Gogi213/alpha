#!/usr/bin/env bash
# TK-289: постановка symclose сита П-18 на calc (prod, 9 ядер, PM=9) — выполняется на calc.
python3 /data/sched/alsched.py submit --cls prod --name tk289-symclose --max-runtime 60m --cores 9 --mem 40 --disk none --cwd /data/tk0289 \
  --adhoc "TK-289: closes с монетой для сита П-18 (symclose по f1/f6b/f6v/f8, psim cap 0, без движка); сито и F2 — следующий шаг" \
  --reason "повтор известного psim (f8-psim 37 мин на 1 ядро, 8 окон) с --closes-sym-out; пробы нет, результат тот же, что у tk308-symclose" \
  -- env PM=9 bash /data/tk0289/symclose.sh
