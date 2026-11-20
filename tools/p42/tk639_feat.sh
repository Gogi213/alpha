#!/usr/bin/env bash
# П-42 §13.37 (TK-639): n / v на кандидатах баз h775 / o566 в окнах 1 / 6 / 12 / 24 ч (12 прогонов `alpha-feat tsize`), ворота G-n39 / G-reg, затем пороги и таблицы `--ftab` (`tk639_run.py counts`). НЕ читает P&L.
# Запуск: systemd-run --unit=tk639-feat --collect -q -p Nice=19 -p CPUQuota=400% -p IOSchedulingClass=idle bash /data/p42-tk639/code/tk639_feat.sh ; код возврата — /data/p42-tk639/rc/feat (0 = всё прошло, иначе `шаг:код`).
set -uo pipefail
ROOT=/data/p42-tk639; cd "$ROOT/code"; mkdir -p "$ROOT/log" "$ROOT/rc"; rm -f "$ROOT/rc/feat"
LOG=$ROOT/log/feat-chain.log
step() { local name=$1; shift; [ -e "$ROOT/rc/step-$name" ] && return 0; echo "$(date -u +%T) $name" >> "$LOG"; PYTHONUTF8=1 python3 "$@" > "$ROOT/log/$name.log" 2>&1; local rc=$?; echo "$(date -u +%T) $name rc=$rc" >> "$LOG"; [ $rc -eq 0 ] || { echo "$name:$rc" > "$ROOT/rc/feat"; exit 1; }; touch "$ROOT/rc/step-$name"; }
for uni in h775 o566; do for w in wa wb wc; do for s in L S; do
  step feat-$uni-$w-$s tk639_feat.py build --uni $uni --window $w --side $s --procs 4
done; done; done
step gate tk639_feat.py gate
step counts-h tk639_run.py counts --uni h775
step counts-o tk639_run.py counts --uni o566
echo 0 > "$ROOT/rc/feat"
