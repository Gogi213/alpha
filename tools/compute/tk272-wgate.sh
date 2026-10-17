#!/usr/bin/env bash
# TK-272: гейт колонок multi-W — alpha-tk272d-pgo --tape-log-ws 5,6,8,10,15,60 против готовых лент w5…w60 (--tape-log W):
# по одним суткам каждого месяца янв–сен, те же сделки B1. Колонки rounds.csv до ленты равны, tape_press_lots_w<W> == tape_press_lots у ленты W.
# rc: 1 сбой счёта · 2 расхождение · 3 нет файла/пусто. env: GT=3
set -uo pipefail
T=/data/tk0272; G=$T/wgate; mkdir -p $G; rm -rf $G/20*
export TL=ws GT=${GT:-3} BIN=/opt/alpha-compute/bin/alpha-tk272d-pgo
sed 's#O=/data/p12r3a/w$TL/$d#O='$G'/$d#; s#--tape-log $TL#--tape-log-ws 5,6,8,10,15,60#' /data/tk0115/tk120-thr-w-day.sh > $G/day.sh
grep -q 'tape-log-ws' $G/day.sh && grep -q "O=$G" $G/day.sh || exit 1
awk -F, 'NR>1{print $1, $2}' /data/tk0115/delta/b1-symdays.csv | sort -u | awk '!s[$1]++' | while read -r m d; do bash $G/day.sh $m $d; done
python3 - <<'P'
import csv, glob, os, sys
G = "/data/tk0272/wgate"; WS = (5, 6, 8, 10, 15, 60); bad = nz = 0; days = sorted(d for d in os.listdir(G) if d.startswith("20"))
for d in days:
    if os.path.exists(f"{G}/{d}/fail.txt"): print(d, "GRID_FAIL"); bad += 1
    for f in sorted(glob.glob(f"{G}/{d}/**/rounds*.csv", recursive=True)):
        rel = os.path.relpath(f, f"{G}/{d}")
        new = [r for r in csv.reader(l for l in open(f) if not l.startswith("#"))]
        h = new[0]
        for w in WS:
            of = f"/data/p12r3a/w{w}/{d}/{rel}"
            if not os.path.exists(of): print(d, w, "нет", of); sys.exit(3)
            old = [r for r in csv.reader(l for l in open(of) if not l.startswith("#"))]
            ho = old[0]; k = ho.index("tape_press_lots"); kn = h.index(f"tape_press_lots_w{w}")
            ok = len(old) == len(new) and h[:k] == ho[:k] and all(a[:k] == b[:k] and a[k] == b[kn] for a, b in zip(old[1:], new[1:]))
            nz += sum(1 for b in new[1:] if float(b[kn]) != 0)
            if not ok: bad += 1; print(d, rel, "W", w, "РАСХОЖДЕНИЕ строк old/new", len(old), len(new))
print("суток", len(days), "расхождений", bad, "ненулевых значений ленты", nz)
sys.exit(2 if bad else (3 if not days or nz == 0 else 0))
P
rc=$?; [ $rc = 0 ] && echo "WGATE: identical"; exit $rc
