#!/usr/bin/env bash
# TK-115 R2-B на сутки: ТОЛЬКО символы B1 (b1-symdays.csv); touches --r1-cols --wall-log, затем ОДИН bounce-grid --cells на 31 клетку
# (e112 ×12, e116 ×12, e133 ×3, e65 ×2, g95 sw4/sw5 ×2). Клетки по месяцу: Q — из thr/e-thresholds-<ГГГГ-ММ>.csv (ключи e112_W<W>_q<q>, e116_W<W>_q<q>;
# для W=30 допустим прежний e112_q33/q67), W_ms_median → chase = {0,5;1;2}×W. Нет ключа — отказ ДО счёта (см. tk115-r2b-run.sh).
#   tk115-r2b-day.sh <метка> <мес> <сутки>   env: BIN (нужна сборка с feature r2) TP=4 GT=2 THR=/data/p12r3a/thr5 O=/data/p12r3a/r2b
set -uo pipefail
lab=$1; MON=$2; d=$3
BIN=${BIN:-/data/tk0115/bin/alpha-tk115f}; TP=${TP:-4}; GT=${GT:-2}; THR=${THR:-/data/p12r3a/thr5}; OUT=${O:-/data/p12r3a/r2b}
H=/data/tk046/$MON/home; E=$H/alpha/epochs/e-$MON; O=$OUT/$lab; V=/data/tk044/final3/verdict.csv; B1=${B1:-/data/tk0115/delta/b1-symdays.csv}
mkdir -p "$O"; if [ -e "$O/done" ] || ! mkdir "$O/claim" 2>/dev/null; then exit 0; fi
mkdir -p "$O/log" "$O/out"; : > "$O/fail.txt"
export HOME=$H BIN O TP
ym=2026-$(case $MON in jan) echo 01;; feb) echo 02;; mar) echo 03;; apr) echo 04;; may) echo 05;; jun) echo 06;; jul) echo 07;; aug) echo 08;; sep) echo 09;; oct) echo 10;; esac)
syms=$(awk -F, -v m="$MON" -v d="$d" 'NR>1 && $1==m && $2==d{print $3}' "$B1" | sort -u)
W=$O/w-$d; rm -rf "$W"; mkdir -p "$W/study/approaches" "$W/b5" "$W/bin" "$W/wt"
ln -s "$BIN" "$W/bin/alpha-tk044k1-new"
for x in "$E"/* "$E"/.[!.]*; do b=$(basename "$x"); case $b in study|b5|b5-ref|b5-solo|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -s "$(readlink -f "$x")" "$W/$b"; done
for x in "$E"/study/*; do b=$(basename "$x"); case $b in root-*|approaches) continue;; esac; ln -s "$(readlink -f "$x")" "$W/study/$b"; done
ln -s "$(readlink -f "$E/study/root-$d")" "$W/study/root-$d"
awk -F, -v d="$d" 'NR==FNR{s[$1]=1;next} FNR==1||($2==d&&($1 in s))' <(echo "$syms") "$V" > "$O/verdict-b1.csv"
# клетки месяца: файл --cells и словарь форм (--exit-form …) одним генератором
python3 - "$THR/e-thresholds-$ym.csv" "$O/cells.txt" "$O/exit-forms.txt" <<'PY' || { echo "$d THR_FAIL" >> "$O/fail.txt"; exit 4; }
import csv, sys
thr = {r[0]: r[1] for r in csv.reader(open(sys.argv[1])) if len(r) == 2}
B = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
cells, forms, miss = [], [], []
def add(name, form=None):
    cells.append(f"{name} t-bid-btc4h-q1")
    if form and form not in forms: forms.append(form)
for fam, pre in (("e112", "tape"), ("e116", "cxl")):
    for w in (15, 30, 60):
        for q in (33, 50, 67, 80):
            v = thr.get(f"{fam}_W{w}_q{q}") or (thr.get(f"{fam}_q{q}") if w == 30 else None)
            if v is None: miss.append(f"{fam}_W{w}_q{q}"); continue
            f = f"{pre}{w}q{float(v)!r}"; add(f"{B}-{f}", f)
wm = thr.get("W_ms_median")
if wm is None: miss.append("W_ms_median")
else:
    for a in (0.5, 1, 2):
        f = f"chase{int(round(float(wm) * a))}"; add(f"{B}-{f}", f)
for f in ("wall2", "wall2x"): add(f"{B}-{f}", f)
for sw in (4, 5): add(f"ladder3x0..0.0409sw{sw}-pct2-tr1x1-14400-ttl1800")
if miss: sys.exit("нет порогов: " + " ".join(miss))
open(sys.argv[2], "w").write("\n".join(cells) + "\n"); open(sys.argv[3], "w").write("\n".join(forms) + "\n")
PY
walls() {
  for s in $syms; do echo "$s"; done | xargs -P "$TP" -I{} bash -c 'mkdir -p '"$W"'/wt/sub/D20/'"$d"'; nice -n 5 "$BIN" lob touches --root '"$W"'/study/root-'"$d"' --symbol {} --h3-mode notional --h3-usd 10000 --approach-bps 20 --r1-cols --wall-log --out '"$W"'/wt/sub/D20/'"$d"'/touches-{}.csv > '"$O"'/log/'"$d"'-{}.sub.log 2>&1 || echo "'"$d"' {} TOUCHES_FAIL" >> '"$O"'/fail.txt'
}
t0=$(date +%s); walls; t1=$(date +%s)
common=(--signal approach --queue-model prob:3 --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000 --regime-from study/regime --order-usd 500 --carry-root root --h3-mode notional --h3-usd 10000 --entry-ttl-secs 1800 --band-exit-bps 20 --busy-skip off --threads "$GT" --hold-step skip --exit-group on --events wide --sigma-from study/sigma240)
xf=(--exit-form none); while read -r f; do [ -n "$f" ] && xf+=(--exit-form "$f"); done < "$O/exit-forms.txt"
(cd "$W" && nice -n 5 "$BIN" lob bounce-grid --verdict-csv "$O/verdict-b1.csv" --root "study/root-$d" --touches-from wt/sub/D20 "${common[@]}" --r1-cols \
  --entry-form ladder3x0..0.0409sw2 --entry-form ladder3x0..0.0409sw4 --entry-form ladder3x0..0.0409sw5 \
  --stop-form pct2 --take-form tr1x1 --deadline-secs 14400 "${xf[@]}" \
  --set t-bid-btc4h-q1:age=2700,side=bid,btc4h_max=-44.55 --tape-log 60 --cells "$O/cells.txt" --out-dir "b5/out-$d" > "$O/log/$d.grid.log" 2>&1) || echo "$d GRID_FAIL" >> "$O/fail.txt"
t2=$(date +%s)
mkdir -p "$O/out/$d"; cp -r "$W/b5/out-$d"/. "$O/out/$d"/
echo "$d syms_b1=$(echo "$syms"|grep -c .) touches_s=$((t1-t0)) grid_s=$((t2-t1))" >> "$OUT/units.txt"
rm -rf "$W"; touch "$O/done"
