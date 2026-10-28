#!/bin/bash
# TK-436 (возврат 3): профиль tk422-features на кубе — где 334 с из 359 сквозной v11f. Срез: каждое 4-е окно среза гейта (≈680), 1 процесс, cProfile.
#   alsched submit --kind gate --name tk436-f422prof --cores 1 --max-runtime 20m -- bash /data/tk0436/tk436-f422prof.sh
cd /data/tk0433/v11f || exit 9
PY=/data/tk421/venv/bin/python3
O=/data/tk0436/f422prof
rm -rf $O; mkdir -p $O
awk 'NR==1||(NR-2)%4==0' /data/tk0436/f422gate/win.csv > $O/win.csv
$PY -m cProfile -o $O/prof.bin /data/tk0436/tk422-features.py $O/win.csv $O/new.csv --procs 1 --aug-candles /srv/alpha-sda/candles-1m-aug --instr /srv/alpha-sda/tk423/cov/bybit-instruments.tsv --cube /data/tk0436/cube3 --cube-trades /data/tk0436/cube3t --cube-from 2026-08-01T00:00 > $O/new.log 2> $O/new.err
$PY -c "
import pstats,sys
p=pstats.Stats('$O/prof.bin'); p.sort_stats('tottime').print_stats(30)" > $O/top-tottime.txt 2>&1
$PY -c "
import pstats,sys
p=pstats.Stats('$O/prof.bin'); p.sort_stats('cumtime').print_stats(40)" > $O/top-cumtime.txt 2>&1
echo 0 > $O/done
