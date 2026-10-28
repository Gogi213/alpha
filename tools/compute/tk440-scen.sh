#!/bin/bash
# TK-440 шаг 6: сценарии TK-438 (доли S1–S11 по D×знак; конец пампа агрегатом, отсчёт кD) по месяцам янв–июль — тот же код/пороги, без подбора.
# Бегут 7 месяцев параллельно (16 потоков). Метка scen.done, код scen.rc; по месяцу scen/sM-shares.csv, scen/aM-agg.csv.
#   alsched submit --kind work --name tk440-scen --cores 15 --max-runtime 120m --est 45m -- bash /data/tk0440/tk440-scen.sh
cd /data/tk0440 || exit 9; mkdir -p scen; rm -f scen.done scen.rc
PY=/data/tk421/venv/bin/python3
XQ=/data/tk0433/v11/xq.json
cp /data/tk0440/tk438-scen.py /data/tk0440/tk426-classes.py /data/tk0440/tk440-scen.py . 2>/dev/null
for M in 01 02 03 04 05 06 07; do
  R=/srv/alpha-sda/candles-1m-2026$M; F=feat/f-$M/features-2026-$M.csv
  ( [ -s scen/s$M-shares.csv ] || MODE=shares NB=3000 $PY tk440-scen.py $M $F $XQ $R scen/s$M > scen/s$M.log 2>&1
    echo $? > scen/s$M.rc ) &
  ( [ -s scen/a$M-agg.csv ] || MODE=agg NB=1000 $PY tk440-scen.py $M $F $XQ $R scen/a$M > scen/a$M.log 2>&1
    echo $? > scen/a$M.rc ) &
done
wait
if [ "$(cat scen/*.rc | sort -u | tr -d '\n')" = 0 ]; then echo 0 > scen.rc; else echo 1 > scen.rc; fi
touch scen.done
