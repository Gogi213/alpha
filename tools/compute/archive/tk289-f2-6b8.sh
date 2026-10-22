#!/usr/bin/env bash
# TK-289 П-18 F2 осей 6б и 8 (6 + 6 клеток × 9 мес. = 108 хеш-мес.; сетка docs/findings/tk289-f2-grid-2026-10-21.csv): пороги 6б для 6 новых квантилей (по сигналам B1 из f1/out),
#   σ-таблицы 6 окон (как sigma-pad.sh, свечи sig2/klines), один xargs по суткам: проход 6б (b6) + по проходу на окно σ, затем psim и склейка -> /data/tk0289/f2b/psim, сводка f2b-sum.csv.
#   Выход /data/tk0289/f2b/<b6|окно>/out/run-<мес>-<сутки>/ ; env NX (=ядра, умолч. 15), GT=1. Повтор — дельта (готовые пороги/таблицы/сутки не трогаются). Маркер: f2b/done (rc 0).
set -uo pipefail
T=/data/tk0289; R=$T/f2b; G=$T/f2-grid.csv; WS="266 294 325 387 416 447"; QS=70,73,77,82,85,87
export REASON="TK-289 П-18 F2 оси 6б и 8, 12 клеток, pgo" BIN=/opt/alpha-compute/bin/alpha-tk0289b-pgo SCRIPTS=$T/scripts NX=${NX:-15} GT=${GT:-1} PLAN=L TP=1 R T
mkdir -p "$R/thr"; rm -f "$R/done"
# 1. пороги 6б (три группы месяцев) и σ-таблицы 6 окон × 9 мес. — одновременно
thr() { for m in "$@"; do [ -e "$R/thr/m-$m.json" ] || python3 $T/tk289-thr6b.py $T/f1/out "$R/thr/m-$m.json" "$R/thr/m-$m.csv" --qs $QS --month 2026-$m > "$R/thr/m-$m.log" 2>&1 || return 1; done; }
sigone() { local i=$1 W=$2 ms=(jan feb mar apr may jun jul aug sep) m out E syms
  m=${ms[$i]}; out=$T/sig2/sigma$W/$m; [ -e "$out.ok" ] && return 0
  E=/data/tk046/$m/home/alpha/epochs/e-$m/study/sigma240; syms=$(ls "$E" | sed 's/^sigma-//;s/\.csv$//' | paste -sd,)
  [ -e "$T/sig2/klines/$m.ok" ] || return 1
  rm -rf "$out"; mkdir -p "$T/sig2/sigma$W"
  python3 $T/sigma-table.py --klines "$T/sig2/klines/$m" --out-dir "$out" --symbols "$syms" --window-min "$W" > "$out.log" 2>&1 && touch "$out.ok"; }
export -f sigone; export T
(thr 03 02 09 & thr 07 01 05 & thr 08 04 06 & wait) &
tp=$!
for i in 0 1 2 3 4 5 6 7 8; do for w in $WS; do echo "$i $w"; done; done | xargs -P 9 -L1 bash -c 'sigone $0 $1' || { echo "σ-таблицы: сбой"; exit 4; }
wait $tp
ls "$R"/thr/m-*.json 2>/dev/null | wc -l | grep -qx 9 || { echo "пороги 6б: не 9 месяцев"; exit 5; }
python3 - "$R/thr" <<'PY' || exit 5
import glob, json, sys
d = sys.argv[1]; res = {}
for f in sorted(glob.glob(f"{d}/m-*.json")):
    res.update(json.load(open(f, encoding="utf-8")))
json.dump(res, open(f"{d}/strength.json", "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1, sort_keys=True)
rows = []
for i, f in enumerate(sorted(glob.glob(f"{d}/m-*.csv"))):
    rows += open(f, encoding="utf-8").read().splitlines()[0 if i == 0 else 1:]
open(f"{d}/P-18-thresholds-f2.csv", "w", encoding="utf-8", newline="\n").write("\n".join(rows) + "\n")
PY
cat "$R"/thr/m-*.log | sort > "$R/thr/run.log"
# 2. планы: b6 — 6 порогов; по окну σ — одна клетка B1
mkplan() { local d=$1; shift; rm -rf "$d/plan"; python3 $T/p18_plan.py "$d/plan" "$@" --label L --f2 "$G" > /dev/null || return 1
  : > "$d/plan/L.cells"; : > "$d/plan/L.args"; echo 2026-01 > "$d/plan/months.txt"; mkdir -p "$d/out"
  for x in "$d"/out/run-*; do [ -d "$x" ] && [ ! -e "$x/done" ] && rm -rf "$x"; done; return 0; }
mkplan "$R/b6" --axes 6b --strength-json "$R/thr/strength.json" || exit 6
for w in $WS; do mkplan "$R/$w" --axes 8 --only "$w" || exit 6; done
# 3. проход по суткам: 7 единиц (b6 + 6 окон) на сутки
DAYS=/data/tk0341/keep/days9.txt
while read -r m d; do for w in b6 $WS; do echo "$w $m $d"; done; done < $DAYS > "$R/units.txt"
for w in b6 $WS; do cp $DAYS "$R/$w/days.txt"; done
xargs -P "$NX" -L1 bash -c 'w=$1; s=; r=; [ "$w" = b6 ] || { s=sigma$w; r=$T/sig2/sigma$w; }; SIG=$s SIGROOT=$r P=$R/$w/plan O=$R/$w/out bash "$SCRIPTS/day-cells.sh" "run-$2-$3" "$2" "$3"' _ < "$R/units.txt"
rc=$?
n=$(cat "$R"/*/out/*/fail.txt 2>/dev/null | wc -l)
[ "$n" = 0 ] || { echo "fail.txt: $n строк"; exit 3; }
[ "$rc" = 0 ] || exit $rc
# 4. psim по проходам, склейка (имена вариантов разные: p18a6bv<q>, p18a8v<окно>), сводка
export PM=4
for w in b6 $WS; do bash /opt/alpha-launch/tools/launch/psim-launch.sh "$R/$w" || exit 7; done
mkdir -p "$R/psim"
python3 - "$R" b6 $WS <<'PY' || exit 8
import json, sys
R, ds = sys.argv[1], sys.argv[2:]
for m in range(1, 10):
    for cap in (0, 3):
        fn = f"closes-p14f1-cap{cap}-2026-{m:02d}.json"; out = {}
        for d in ds:
            x = json.load(open(f"{R}/{d}/psim/{fn}", encoding="utf-8"))
            dup = set(x) & set(out)
            assert not dup, (d, fn, dup)
            out.update(x)
        json.dump(out, open(f"{R}/psim/{fn}", "w", encoding="utf-8"), separators=(",", ":"))
PY
python3 /data/tk0231/sum_closes_f1.py "$R/psim" $T/f2b-sum.csv closes-p14f1 || exit 9
echo ok > "$R/done"
