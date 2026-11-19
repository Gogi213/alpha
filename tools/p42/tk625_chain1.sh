#!/usr/bin/env bash
# П-42 §13.32 (TK-625): цепочка счёта без P&L на srv — после `tk625-epall` (события o566): окно w0 честной вселенной, дампы и базы h775, события h775, ворота, пороги и таблицы признаков.
# Запуск: systemd-run --unit=tk625-chain1 --collect -q -p Nice=19 -p CPUQuota=400% -p IOSchedulingClass=idle bash /data/p42-tk625/code/tk625_chain1.sh ; код возврата — /data/p42-tk625/rc/chain1.
set -uo pipefail
ROOT=/data/p42-tk625; cd "$ROOT/code"; mkdir -p "$ROOT/log" "$ROOT/rc"; rm -f "$ROOT/rc/chain1"
LOG=$ROOT/log/chain1.log
step() { local name=$1; shift; [ -e "$ROOT/rc/step-$name" ] && return 0; echo "$(date -u +%T) $name" >> "$LOG"; PYTHONUTF8=1 python3 tk625_run.py "$@" > "$ROOT/log/$name.log" 2>&1; local rc=$?; echo "$(date -u +%T) $name rc=$rc" >> "$LOG"; [ $rc -eq 0 ] || { echo "$name:$rc" > "$ROOT/rc/chain1"; exit 1; }; touch "$ROOT/rc/step-$name"; }
while systemctl is-active -q tk625-epall; do sleep 30; done
for w in w0 wa wb wc; do [ -s "$ROOT/ev/$w.csv.stats.json" ] || { echo "нет событий o566 $w" >> "$LOG"; echo "ev-o566-$w:missing" > "$ROOT/rc/chain1"; exit 1; }; done
if [ ! -e "$ROOT/h775/w0/READY" ]; then
  echo "$(date -u +%T) prep h775 w0" >> "$LOG"; bash tk625_prep.sh w0 >> "$LOG" 2>&1
  [ "$(cat "$ROOT/h775/w0/READY" 2>/dev/null)" = 0 ] || { echo "prep:fail" > "$ROOT/rc/chain1"; exit 1; }
fi
step coindays-h coindays --uni h775
step engall-h engall --uni h775
step epall-h epall --uni h775
step gates1-o gates1 --uni o566
step gates1-h gates1 --uni h775
step g4x g4x
step geq geq
step g3-o g3 --uni o566
step g3-h g3 --uni h775
step counts-h counts --uni h775
python3 - <<'PY' >> "$LOG" 2>&1
import json
cm = {}
for s in ("L", "S"):
    r = json.load(open("/data/p42-tk625/res/counts-h775-%s.json" % s, encoding="utf-8"))
    cm[s] = {c["cid"]: c["c"] for c in r["cells"]}
json.dump(cm, open("/data/p42-tk625/res/cmap-h775.json", "w"), indent=1)
PY
step counts-o counts --uni o566 --cmap "$ROOT/res/cmap-h775.json"
echo 0 > "$ROOT/rc/chain1"
