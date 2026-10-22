#!/usr/bin/env bash
# TK-319: продолжение tk319-fixklines.sh (оборван по max-runtime 2 ч на шаге «подходы против касаний» — читал касания всех
# 25025 монето-суток, 61 ГБ HDD под g102b). Свечи/σ240/режим готовы (fixklines/RESULT.txt). Здесь: (1) S0 на 5 монетах дня
# с самым глубоким провалом BTC каждого месяца (events > 0); (2) подходы против касаний — выборка: 2 суток месяца
# (лучшие по провалу BTC и 15-е), все символы. Результат дописывается в $K/fixklines/RESULT2.txt, метка $K/fixklines/done2.
set -uo pipefail
K=/data/tk0319; B=$K/bin; BIN=${BIN:-/opt/alpha-compute/bin/alpha-tk312-pgo}; F=$K/fixklines
R=$F/RESULT2.txt; : > "$R"
say() { echo "$*" | tee -a "$R"; }
sum() { awk '{s+=$1} END{print s+0}'; }
# ---- S0 на 5 монетах дня с самым глубоким провалом BTC каждого месяца
SP=$F/splan; mkdir -p "$SP"; bash "$B/tk313-spot-plan.sh" "$SP/full" > /dev/null
grep -v '^--set' "$SP/full/spot.args" > "$SP/spots0.args"; grep '^--set s0:' "$SP/full/spot.args" >> "$SP/spots0.args"
grep -E ' s0$' "$SP/full/spot.cells" > "$SP/spots0.cells"
for m in 01 02 03 04 05 06 07 08 09; do : > "$SP/spots0-2026-$m.cells"; : > "$SP/spots0-2026-$m.args"; done
printf 'month,day,symbol\n' > "$F/s0-days.csv"
for m in jan feb mar apr may jun jul aug sep; do
  d=$(python3 - "$K" "$m" <<'PY'
import csv, glob, os, sys
K, m = sys.argv[1:3]
best = (-1, None)
for f in sorted(glob.glob(f"{K}/regime/{m}/2026-*.csv")):
    c = sum(1 for r in csv.DictReader(open(f)) if r["btc_ret_4h_bps"] != "" and float(r["btc_ret_4h_bps"]) <= -44.55)
    if c > best[0]: best = (c, os.path.basename(f)[:-4])
print(best[1])
PY
)
  for s in $(ls -S "$K/jrn/$d/wt/sub/D20/$d"/touches-*.csv | head -8); do b=${s##*/touches-}; b=${b%.csv}; grep -q ",$d,$b\$" "$K/b1-symdays.csv" && echo "$m,$d,$b" >> "$F/s0-days.csv"; done
done
rm -rf "$F/s0"
awk -F, 'NR>1{print $1" "$2}' "$F/s0-days.csv" | sort -u | while read -r m d; do
  grep -E "^($m,$d,|month)" "$F/s0-days.csv" | awk -F, 'NR==1||c++<5' > "$F/b1-$d.csv"
  PLAN=spots0 P=$SP O=$F/s0 EPOCH=$K/e-$m VERDICT=$K/verdict.csv B1=$F/b1-$d.csv JRN=$K/jrn RG=$K/regime BIN=$BIN GT=1 TP=2 bash "$B/day-cells.sh" "$m-$d" $m $d > /dev/null 2>&1
  ev=$(cat "$F/s0/$m-$d/log/"*.log 2>/dev/null | grep -o 'events=[0-9]*' | cut -d= -f2 | sum); rd=$(cat "$F/s0/$m-$d/log/"*.log 2>/dev/null | grep -o 'rounds=[0-9]*' | cut -d= -f2 | sum)
  say "S0 $m $d: монет $(($(wc -l < "$F/b1-$d.csv") - 1)), events=$ev, rounds=$rd, fail=$(wc -c < "$F/s0/$m-$d/fail.txt" 2>/dev/null)"
done
# ---- подходы против касаний: выборка (лучшие сутки S0 + 15-е числа), все символы суток
python3 - "$K" "$R" <<'PY'
import csv, glob, os, sys
K, R = sys.argv[1], sys.argv[2]
out = open(R, "a")
def lines(p):
    try:
        with open(p, "rb") as f: return max(sum(1 for _ in f) - 1, 0)
    except OSError: return -1
best = {r["month"]: r["day"] for r in csv.DictReader(open(f"{K}/fixklines/s0-days.csv"))}
days = {}
for r in csv.DictReader(open(f"{K}/b1-symdays.csv")):
    days.setdefault(r["month"], {}).setdefault(r["day"], []).append(r["symbol"])
for m in "jan feb mar apr may jun jul aug sep".split():
    pick = {best.get(m)} | {d for d in days.get(m, {}) if d.endswith("-15")}
    n = nt = na = st = sa = nf = 0
    for d in sorted(x for x in pick if x in days.get(m, {})):
        for s in days[m][d]:
            base = f"{K}/jrn/{d}/wt/sub/D20/{d}"
            t, a = lines(f"{base}/touches-{s}.csv"), lines(f"{base}/approaches-{s}.csv")
            n += 1
            if t < 0 or a < 0: nf += 1; continue
            nt += t > 0; na += a > 0; st += t; sa += a
    s = f"подходы {m} (выборка {sorted(x for x in pick if x)}): монето-суток {n}, без файла {nf}, с касаниями {nt}, с подходами {na}, строк касаний {st}, строк подходов {sa}"
    print(s); out.write(s + "\n")
PY
touch "$F/done2"
