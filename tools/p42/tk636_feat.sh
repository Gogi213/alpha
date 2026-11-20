#!/usr/bin/env bash
# П-42 §13.35 (TK-636): признаки монеты на кандидатах баз h775 / o566 (build + ворота G-eq и G3x). НЕ читает P&L.
# Запуск: systemd-run --unit=tk636-feat --collect -q -p Nice=19 -p CPUQuota=400% -p IOSchedulingClass=idle bash /data/p42-tk636/code/tk636_feat.sh ; код возврата — /data/p42-tk636/rc/feat.
set -uo pipefail
ROOT=/data/p42-tk636; cd "$ROOT/code"; mkdir -p "$ROOT/log" "$ROOT/rc"; rm -f "$ROOT/rc/feat"
LOG=$ROOT/log/feat-chain.log
for uni in h775 o566; do for cmd in build g3x; do
  name=$cmd-$uni
  [ -e "$ROOT/rc/step-$name" ] && continue
  echo "$(date -u +%T) $name" >> "$LOG"
  PYTHONUTF8=1 python3 tk636_feat.py $cmd --uni $uni > "$ROOT/log/$name.log" 2>&1
  rc=$?
  echo "$(date -u +%T) $name rc=$rc" >> "$LOG"
  [ $rc -eq 0 ] || { echo "$name:$rc" > "$ROOT/rc/feat"; exit 1; }
  touch "$ROOT/rc/step-$name"
done; done
echo 0 > "$ROOT/rc/feat"
