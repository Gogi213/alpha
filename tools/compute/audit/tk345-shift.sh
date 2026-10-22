#!/usr/bin/env bash
# TK-345 инвариант (6): заглядывание сдвигом колонок кэша касаний (X исходы / F признаки из будущего / B из прошлого).
#   tk345-shift.sh <сутки> <выход-каталог> [символы через запятую]    env: BIN GT   (alsched --kind gate)
# Вердикт — <выход>/verdict.txt: X равен C (движок не читает исходы); F и B ≠ C у наборов по сдвинутым признакам.
set -uo pipefail
d=$1; O=$2; SYMS=${3:-ORDIUSDT,KAITOUSDT}
BIN=${BIN:-/opt/alpha-compute/bin/alpha-tk0289c-pgo}; GT=${GT:-3}
mon=$(case ${d:5:2} in 01) echo jan;; 02) echo feb;; 03) echo mar;; 04) echo apr;; 05) echo may;; 06) echo jun;; 07) echo jul;; 08) echo aug;; 09) echo sep;; esac)
H=/data/tk046/$mon/home; E=$H/alpha/epochs/e-$mon
export HOME=$H
here=$(cd "$(dirname "$0")" && pwd)
sargs=(); for s in ${SYMS//,/ }; do sargs+=(--symbol "$s"); done
mkdir -p "$O"; rm -f "$O/verdict.txt"
cd "$E" || exit 3
SRC=/data/tk046/$mon/study/approaches/D20
for v in X F B; do
  rm -rf "$O/cache-$v"; mkdir -p "$O/cache-$v/$d"
  python3 "$here/tk345-shift-make.py" "$SRC/$d" "$O/cache-$v/$d" "$v" ${SYMS//,/ } || exit 4
done
for v in C X F B; do
  if [ "$v" = C ]; then tf=study/approaches/D20; else tf=$O/cache-$v; fi
  rm -rf "$O/run-$v"; mkdir -p "$O/run-$v"
  nice -n 5 "$BIN" lob bounce-grid --root "study/root-$d" --day "$d" "${sargs[@]}" --touches-from "$tf" --regime-from study/regime \
    --queue-model prob:3 --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000 \
    --order-usd 500 --carry-root root --h3-mode notional --h3-usd 10000 --band-exit-bps 20 --busy-skip off --threads "$GT" --hold-step skip --exit-group on --events wide \
    --entry-form market --stop-form pct0.5 --take-form 1to1 --deadline-secs 1800 --exit-form none --early-exit-secs off \
    --set base:side=bid --set usd30k:side=bid,usd_min=30000 --set flow100:side=bid,flow=100 --set age1800:side=bid,age=1800 \
    --set ret1hup:side=bid,ret1h_min=0 --set btcup:side=bid,btc4h_min=0 \
    --out-dir "$O/run-$v/out" > "$O/run-$v/grid.log" 2>&1
  echo "run-$v rc=$?" >> "$O/rc.txt"
done
python3 - "$O" > "$O/verdict.txt" <<'PY'
import csv, glob, os, sys
O = sys.argv[1]
def rd(v, s):
    p = os.path.join(O, f"run-{v}", "out", s, "forms.csv")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8", newline="") as f:
        return [l for l in f.read().splitlines() if not l.startswith("#")]
def nsig(rows):
    r = list(csv.DictReader(rows))
    return sum(int(x["n_signals"]) for x in r if x.get("n_signals", "").isdigit()), len(r)
sets = sorted(os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(O, "run-C", "out", "*", "forms.csv")))
print("набор | n_signals C | X (=C?) | F (=C?) | B (=C?)")
for s in sets:
    c = rd("C", s); line = [s]
    n0, _ = nsig(c); line.append(str(n0))
    for v in "XFB":
        x = rd(v, s)
        if x is None:
            line.append("нет файла"); continue
        n1, _ = nsig(x)
        line.append(f"{n1} ({'байт в байт' if x == c else 'ИЗМЕНИЛСЯ'})")
    print(" | ".join(line))
PY
cat "$O/verdict.txt"
