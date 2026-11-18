#!/usr/bin/env bash
# П-42 §13.23 (TK-598): признаки f12 на сделки всех окон (srv, под systemd-run): F — U0 и U1 обеих сторон; июль, август, 14–16.11 — U1.
#   systemd-run --unit=tk598-feat -p Nice=19 -p CPUWeight=1 -p IOSchedulingClass=idle --collect bash /data/p42-tk598/py/tk598_feat_chain.sh
# Результат: /data/p42-tk598/feat/feat-<прогон>-<вариант>.parquet (+ .info.json), журнал feat.log, готовность — файл DONE (код возврата).
# Метки не читаются (из trades.csv берутся только столбцы ключей).
cd /data/p42-tk598/py
PY=/data/tk592/venv/bin/python
OUT=/data/p42-tk598/feat
mkdir -p $OUT
rm -f $OUT/DONE
rc=0
run() {  # <прогон> <окно> <варианты…>
  local r=$1 w=$2; shift 2
  for v in "$@"; do
    echo "$(date -u +%T) $r $v" >> $OUT/feat.log
    $PY tk598_feat.py build --run /data/p42-tk598/out/$r --variant $v --win $w --prod /data/p42-tk598/py --work $OUT --procs 6 >> $OUT/feat.log 2>&1 || { rc=1; echo "FAIL $r $v" >> $OUT/feat.log; }
  done
}
run hold-1 hold L_u1 S_u1
run jul-1 jul L_u1 S_u1
run aug-1 aug L_u1 S_u1
run F-1 F L_u1 S_u1 L_u0 S_u0
echo $rc > $OUT/DONE
