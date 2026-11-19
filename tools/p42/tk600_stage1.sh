#!/usr/bin/env bash
# П-42 §13.25 этап 1 (TK-600): после признаков окна F и моделей TK-598 — юнит-тесты и ворота (до любых меток), таблицы X, подгонка X-руки, OOF обеих рук, сравнение.
# Запуск: systemd-run --unit=tk600-stage1 -p Nice=19 --collect bash /data/p42-tk600/py/tk600_stage1.sh ; готовность — файл /data/p42-tk600/stage1/DONE (0 — успех).
set -uo pipefail
W=/data/p42-tk600; C=$W/py; PY=/data/tk592/venv/bin/python; T598=/data/p42-tk598; PROCS="${PROCS:-5}"
mkdir -p $W/stage1 $W/tab $W/oof $W/fit-X $W/log; cd $C || exit 1
log() { echo "$(date -u +%T) $*" | tee -a $W/log/stage1.log; }
fail() { log "FAIL $*"; echo 1 > $W/stage1/DONE; exit 1; }
rm -f $W/stage1/DONE
timeout 21600 bash -c "until [ -e $W/feat/DONE-F ]; do sleep 30; done" || fail wait-features-F
[ "$(cat $W/feat/DONE-F)" = 0 ] || fail features-F
log "== ворота до меток"
$PY -m unittest test_tk600 > $W/stage1/unittest.log 2>&1 || fail unittest
tail -3 $W/stage1/unittest.log | tee -a $W/log/stage1.log
export TK594_TRADES=/data/aggtrades/binance
for v in L_u0 S_u0; do
  $PY tk600_gate.py --feat $W/feat/feat-X-F-1-$v.parquet --eps $T598/feat/eps-F-1-$v.parquet --base-date 2026-09-01 --out $W/stage1/gate-T-F-$v.json >> $W/log/stage1.log 2>&1 || fail "G-T $v"
done
cat $W/feat/gate-R-F.json | tee -a $W/log/stage1.log
$PY tk600_compare.py coverage --feat-dir $W/feat --out $W/stage1/coverage-F.csv >> $W/log/stage1.log 2>&1 || fail coverage
log "== ждём модели TK-598 (fit-long.json, fit-short.json)"
timeout 21600 bash -c "until [ -e $T598/fit/fit-long.json ] && [ -e $T598/fit/fit-short.json ]; do sleep 60; done" || fail wait-tk598-models
log "== таблицы X"
for v in L_u0 L_u1 S_u0 S_u1; do
  $PY tk600_fit.py table --run $T598/out/F-1 --variant $v --win F --feat $W/feat/feat-X-F-1-$v.parquet --path $T598/path/path-F-1-$v.parquet --out $W/tab/tab-X-F-$v.parquet >> $W/log/stage1.log 2>&1 || fail "table $v"
  $PY - $W/tab/tab-X-F-$v.parquet $T598/tab/tab-F-$v.parquet <<'PYEOF' || fail "G-K $v"
import sys, pandas as pd
a = pd.read_parquet(sys.argv[1], columns=["sym", "level", "sig_ms"]); b = pd.read_parquet(sys.argv[2], columns=["sym", "level", "sig_ms"])
ka, kb = set(map(tuple, a.to_numpy().tolist())), set(map(tuple, b.to_numpy().tolist()))
assert len(a) == len(b) and ka == kb, (len(a), len(b), len(ka ^ kb))
print("G-K ok", len(a))
PYEOF
done
log "== подгонка X-руки"
for side in long short; do
  u=L; [ $side = short ] && u=S
  $PY tk600_fit.py fit --side $side --u0 $W/tab/tab-X-F-${u}_u0.parquet --u1 $W/tab/tab-X-F-${u}_u1.parquet --out $W/fit-X --procs $PROCS >> $W/log/stage1.log 2>&1 || fail "fit $side"
done
log "== OOF обеих рук"
for side in long short; do
  u=L; [ $side = short ] && u=S
  $PY tk600_oof.py --arm X --side $side --tab-u0 $W/tab/tab-X-F-${u}_u0.parquet --tab-u1 $W/tab/tab-X-F-${u}_u1.parquet --models $W/fit-X/models \
    --out-u0 $W/oof/oof-X-$side-u0.parquet --out-u1 $W/oof/oof-X-$side-u1.parquet >> $W/log/stage1.log 2>&1 || fail "oof X $side"
  $PY tk600_oof.py --arm f12 --side $side --tab-u0 $T598/tab/tab-F-${u}_u0.parquet --tab-u1 $T598/tab/tab-F-${u}_u1.parquet --models $T598/fit/models \
    --out-u0 $W/oof/oof-f12-$side-u0.parquet --out-u1 $W/oof/oof-f12-$side-u1.parquet >> $W/log/stage1.log 2>&1 || fail "oof f12 $side"
done
log "== сравнение (этап 1)"
$PY tk600_compare.py stage1 --tab-dir $W/tab --oof-dir $W/oof --fit-json-x $W/fit-X/fit-long.json,$W/fit-X/fit-short.json --out $W/stage1 > $W/stage1/stage1.txt 2>> $W/log/stage1.log || fail compare
cat $W/stage1/stage1.txt >> $W/log/stage1.log
md5sum $T598/py/tk598_fit.py $C/tk598_fit.py $T598/py/tk598_lib.py $C/tk598_lib.py >> $W/log/stage1.log 2>&1
echo 0 > $W/stage1/DONE
log "ЭТАП 1 ГОТОВ"
