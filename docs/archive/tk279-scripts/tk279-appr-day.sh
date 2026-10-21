#!/usr/bin/env bash
# TK-279 п.1а: замер пересчёта подходов (repeat_count_at_arm) за 1 сутки новым бинарём + сверка со старым журналом (старые колонки равны).
#   tk279-appr-day.sh [сутки] [мес]   env: BIN (обязателен), TP=3, O=/data/tk0279/appr1
set -uo pipefail
d=${1:-2026-03-16}; MON=${2:-mar}; BIN=${BIN:?BIN обязателен}; TP=${TP:-3}; O=${O:-/data/tk0279/appr1}
H=/data/tk046/$MON/home; JD=/data/p12r3a/jrn/$d/wt/sub/D20/$d; N=$O/new/$d
RS=$(readlink -f "$H/alpha/epochs/e-$MON/study/root-$d"); AR=/srv/alpha-sda/root/root-$d; [ -d "$AR" ] && RS=$AR
rm -rf "$O"; mkdir -p "$N" "$O/log"; export HOME=$H BIN RS N O
syms=$(ls "$JD"/touches-*.csv | sed 's#.*/touches-##; s#\.csv$##')
t0=$(date +%s)
for s in $syms; do echo "$s"; done | xargs -P "$TP" -I{} bash -c 'nice -n 5 "$BIN" lob touches --root "$RS" --symbol {} --h3-mode notional --h3-usd 10000 --approach-bps 20 --r1-cols --wall-log --out "$N/touches-{}.csv" > "$O/log/{}.log" 2>&1 || echo "{} TOUCHES_FAIL" >> "$O/fail.txt"'
t1=$(date +%s); echo "day=$d syms=$(echo "$syms" | wc -w) touches_s=$((t1-t0)) tp=$TP bin=$BIN" > "$O/summary.txt"
python3 - "$JD" "$N" >> "$O/summary.txt" <<'P'
import csv, sys, os, glob
jd, nd = sys.argv[1:3]; bad = 0; tot = 0; rep = 0; newc = set()
for f in sorted(glob.glob(nd + '/approaches-*.csv')):
    old = os.path.join(jd, os.path.basename(f))
    if not os.path.exists(old): print('NO_OLD', f); bad += 1; continue
    a = list(csv.DictReader(open(old))); b = list(csv.DictReader(open(f)))
    oc = list(a[0].keys()) if a else list(csv.reader(open(old)))[0]
    nc = list(b[0].keys()) if b else list(csv.reader(open(f)))[0]
    newc |= set(nc) - set(oc)
    if len(a) != len(b): print('ROWS', os.path.basename(f), len(a), len(b)); bad += 1; continue
    for x, y in zip(a, b):
        if any(x[c] != y.get(c) for c in oc): print('DIFF', os.path.basename(f), x['arm_ms']); bad += 1; break
    tot += len(b); rep += sum(1 for y in b if y.get('repeat_count_at_arm', '0') not in ('', '0'))
tb = 0
for f in sorted(glob.glob(nd + '/touches-*.csv')):
    old = os.path.join(jd, os.path.basename(f))
    if os.path.exists(old) and open(old, 'rb').read() != open(f, 'rb').read(): tb += 1
print('new_cols', sorted(newc), 'rows', tot, 'repeat>0', rep, 'old_cols_bad', bad, 'touches_files_differ', tb)
open(nd + '/../cmp.rc', 'w').write(str(bad))
P
[ -s "$O/fail.txt" ] && { cat "$O/fail.txt" >> "$O/summary.txt"; exit 3; }
[ "$(cat "$O/cmp.rc")" = 0 ]
