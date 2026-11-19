#!/usr/bin/env bash
# П-42 §13.33 (TK-633): счёт БЕЗ P&L на srv — таблицы --ftab (свои + чужие столбцы), пороги, дубли, G-eq, G3x. Читает только ключи сделок баз.
# Запуск: systemd-run --unit=tk633-counts --collect -q -p Nice=19 -p CPUQuota=200% -p IOSchedulingClass=idle bash /data/p42-tk633/code/tk633_counts.sh ; код возврата — /data/p42-tk633/rc/counts.
set -uo pipefail
ROOT=/data/p42-tk633; cd "$ROOT/code"; mkdir -p "$ROOT/log" "$ROOT/rc" "$ROOT/res"; rm -f "$ROOT/rc/counts"
LOG=$ROOT/log/counts-chain.log
step() { local name=$1; shift; [ -e "$ROOT/rc/cstep-$name" ] && return 0; echo "$(date -u +%T) $name" >> "$LOG"; PYTHONUTF8=1 python3 tk633_run.py "$@" > "$ROOT/log/$name.log" 2>&1; local rc=$?; echo "$(date -u +%T) $name rc=$rc" >> "$LOG"; [ $rc -eq 0 ] || { echo "$name:$rc" > "$ROOT/rc/counts"; exit 1; }; touch "$ROOT/rc/cstep-$name"; }
step counts-h counts --uni h775
python3 - <<'PY' >> "$LOG" 2>&1
import json
cm = {}
for s in ("L", "S"):
    r = json.load(open("/data/p42-tk633/res/counts-h775-%s.json" % s, encoding="utf-8"))
    cm[s] = {c["cid"]: c["c"] for c in r["cells"]}
json.dump(cm, open("/data/p42-tk633/res/cmap-h775.json", "w"), indent=1)
PY
[ -s "$ROOT/res/cmap-h775.json" ] || { echo "cmap:fail" > "$ROOT/rc/counts"; exit 1; }
step counts-o counts --uni o566 --cmap "$ROOT/res/cmap-h775.json"
step geq-h geq --uni h775
step geq-o geq --uni o566
step g3x-h g3x --uni h775
step g3x-o g3x --uni o566
echo 0 > "$ROOT/rc/counts"
