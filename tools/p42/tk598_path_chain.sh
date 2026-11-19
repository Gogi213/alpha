#!/usr/bin/env bash
# П-42 §13.23 (TK-598), цель T5: MAE / MFE по ленте для сделок окна F (U1, затем U0); остальные окна — только в скрипте чтения (метки запечатаны до заморозки).
#   systemd-run --unit=tk598-path -p Nice=19 -p CPUWeight=1 -p IOSchedulingClass=idle --collect bash /data/p42-tk598/py/tk598_path_chain.sh
# Результат: /data/p42-tk598/path/path-F-1-<вариант>.parquet, журнал path.log, готовность — файл DONE.
cd /data/p42-tk598/py
PY=/data/tk592/venv/bin/python
OUT=/data/p42-tk598/path
mkdir -p $OUT
rm -f $OUT/DONE
rc=0
for v in L_u1 S_u1 L_u0 S_u0; do
  side=long; [ "${v:0:1}" = S ] && side=short
  echo "$(date -u +%T) $v" >> $OUT/path.log
  $PY tk598_path.py run --detail /data/p42-tk598/out/F-1/eng/$v/$side-detail.jsonl --t0 2026-09-01 --agg /data/aggtrades/binance --out $OUT/path-F-1-$v.parquet --procs 6 >> $OUT/path.log 2>&1 || { rc=1; echo "FAIL $v" >> $OUT/path.log; }
done
echo $rc > $OUT/DONE
