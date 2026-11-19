#!/usr/bin/env bash
# П-42 §13.34 (TK-634): сырые признаки подхода и потока на кандидатах баз h775 / o566 (12 сборок). НЕ читает P&L.
# Запуск: systemd-run --unit=tk634-feat --collect -q -p Nice=19 -p CPUQuota=400% -p IOSchedulingClass=idle bash /data/p42-tk634/code/tk634_feat.sh ; код возврата — /data/p42-tk634/rc/feat.
set -uo pipefail
ROOT=/data/p42-tk634; cd "$ROOT/code"; mkdir -p "$ROOT/log" "$ROOT/rc"; rm -f "$ROOT/rc/feat"
LOG=$ROOT/log/feat-chain.log
for uni in h775 o566; do for w in wa wb wc; do for s in L S; do
  name=feat-$uni-$w-$s
  [ -e "$ROOT/rc/step-$name" ] && continue
  echo "$(date -u +%T) $name" >> "$LOG"
  PYTHONUTF8=1 python3 tk634_feat.py build --uni $uni --window $w --side $s --procs 4 > "$ROOT/log/$name.log" 2>&1
  rc=$?
  echo "$(date -u +%T) $name rc=$rc" >> "$LOG"
  [ $rc -eq 0 ] || { echo "$name:$rc" > "$ROOT/rc/feat"; exit 1; }
  touch "$ROOT/rc/step-$name"
done; done; done
echo 0 > "$ROOT/rc/feat"
