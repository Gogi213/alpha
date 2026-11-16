#!/bin/bash
# Сверка окна записей с REST: postcheck.sh <биржа> <каталог данных биржи> <подстрока имени файлов запуска, напр. -1794656680> <t_start epoch> <t_end epoch> <метка>
# Шаги: состав выше порога (univ.py) → REST-минутки окна по составу (klines.py) → матрица записей окна (aggregate.py --only) → postcheck.py.
# Запускать под systemd-run (Nice=19); результаты — /data/perf/poolfix-deploy/<метка>/<биржа>/.
set -eu
EX=$1; ROOT=$2; ONLY=$3; TS=$4; TE=$5; LABEL=$6
P=/data/perf/poolfix-deploy/tools; T=$P
OUT=/data/perf/poolfix-deploy/$LABEL/$EX; mkdir -p $OUT
python3 $P/univ.py --exchange $EX --out $OUT/univ.json
python3 -c "
import json
d = json.load(open('$OUT/univ.json'))
open('$OUT/syms.txt', 'w').write('\n'.join(sorted(s for s, v in d['qv'].items() if v >= d['min_qv'])) + '\n')"
python3 $T/klines.py --exchange $EX --extra-syms $OUT/syms.txt --out $OUT/klines --t-start $TS --t-end $TE > $OUT/klines.log 2>&1
python3 $T/aggregate.py --exchange $EX --root $ROOT --out $OUT/agg --t-start $TS --t-end $TE --procs 2 --only "$ONLY" > $OUT/agg.log 2>&1
python3 $P/postcheck.py --exchange $EX --agg $OUT/agg/$EX.npz --klines $OUT/klines --univ $OUT/univ.json --label $LABEL --out $OUT/result.json | tee $OUT/summary.txt
