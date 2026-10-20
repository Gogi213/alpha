#!/usr/bin/env bash
# TK-319 (дефект 20.10): свечи spot BTCUSDT/ETHUSDT/SOLUSDT начинались с 2026-08-31 — их создал гейт B (--since 08-31), а
# ref-klines.py при существующем файле дописывает вперёд и --since не читает → режим btc4h янв–авг пуст, S0 = 0 сигналов.
# Шаги: (1) три файла свечей — в klines-old, перекачка с 01.01; (2) σ240 SOLUSDT; (3) режим всех суток (месяц = один
# последовательный цикл: regime.py пишет days.csv месяца); (4) проверки → $K/fixklines/RESULT.txt: охват свечей, режим по
# месяцам (минут с btc4h ≤ -44,55), сентябрь до/после, спот против фьючерсного режима, подходы против касаний,
# S0 на 5 монетах дня с самым глубоким провалом BTC каждого месяца (events > 0).
# Запуск: alsched submit --kind work --cores 9 … -- bash /data/tk0319/bin/tk319-fixklines.sh
set -uo pipefail
K=/data/tk0319; B=$K/bin; BIN=${BIN:-/opt/alpha-compute/bin/alpha-tk312-pgo}; F=$K/fixklines
mkdir -p "$F" "$K/klines-old" "$K/regime-before-fix"; R=$F/RESULT.txt; : > "$R"
say() { echo "$*" | tee -a "$R"; }
sum() { awk '{s+=$1} END{print s+0}'; }
[ -e "$K/regime-before-fix/.copied" ] || { cp -a "$K/regime/." "$K/regime-before-fix/" && touch "$K/regime-before-fix/.copied"; }
for s in BTCUSDT ETHUSDT SOLUSDT; do
  f=$K/klines/ref-$s-1m.csv
  [ "$(sed -n 2p "$f" | cut -d, -f1)" = 1767225600000 ] || mv "$f" "$K/klines-old/ref-$s-1m.csv.$(date +%s)"
done
printf '%s\n' BTCUSDT ETHUSDT SOLUSDT | xargs -P3 -I{} bash -c 'python3 "$0/ref-klines.py" --category spot --out-dir "$1/klines" --symbols {} --since 2026-01-01 --until 2026-09-30 > "$2/klines-{}.log" 2>&1' "$B" "$K" "$F"
for s in BTCUSDT ETHUSDT SOLUSDT; do f=$K/klines/ref-$s-1m.csv; say "свечи $s: строк $(($(wc -l < "$f") - 1)) (ждём 393120), с $(sed -n 2p "$f" | cut -d, -f1), по $(tail -1 "$f" | cut -d, -f1); $(tail -1 "$F/klines-$s.log")"; done
python3 "$B/sigma-table.py" --klines "$K/klines" --out-dir "$K/sigma240" --symbols SOLUSDT 2>&1 | tail -1 | tee -a "$R"
say "σ240 SOLUSDT: с $(sed -n 2p "$K/sigma240/sigma-SOLUSDT.csv" | cut -d, -f1)"
# ---- режим: месяцы параллельно, сутки внутри месяца — подряд
for m in jan feb mar apr may jun jul aug sep; do
  ( awk -F, -v m="$m" 'NR>1 && $1==m{print $2}' "$K/b1-symdays.csv" | sort -u | while read -r d; do
      python3 "$B/regime.py" --day "$d" --touches "$K/jrn-view" --regime-dir "$K/regime/$m" > /dev/null 2>> "$F/regime-$m.err"
    done; touch "$F/regime-$m.done" ) &
done
wait
# ---- проверки
python3 - "$K" "$R" <<'PY'
import csv, glob, os, statistics, sys
K, R = sys.argv[1], sys.argv[2]
out = open(R, "a")
def say(s): print(s); out.write(s + "\n")
THR = -44.55
for m in "jan feb mar apr may jun jul aug sep".split():
    files = sorted(glob.glob(f"{K}/regime/{m}/2026-*.csv"))
    n = ok = dip = 0; best = (0, None)
    for f in files:
        rows = list(csv.DictReader(open(f)))
        v = [float(r["btc_ret_4h_bps"]) for r in rows if r["btc_ret_4h_bps"] != ""]
        n += len(rows); ok += len(v)
        c = sum(1 for x in v if x <= THR); dip += c
        if c > best[0]: best = (c, os.path.basename(f)[:-4])
    say(f"режим {m}: суток {len(files)}, минут {n}, с btc4h {ok} ({100*ok/max(n,1):.1f}%), минут btc4h<={THR}: {dip}, лучшие сутки {best[1]} ({best[0]})")
same = diff = 0
for f in glob.glob(f"{K}/regime/sep/2026-*.csv"):
    g = f.replace("/regime/", "/regime-before-fix/")
    if os.path.exists(g):
        if open(f, "rb").read() == open(g, "rb").read(): same += 1
        else: diff += 1
say(f"режим sep до/после: совпало {same}, разошлось {diff}")
for m in "jan apr aug".split():
    fs = sorted(glob.glob(f"{K}/regime/{m}/2026-*.csv"))[10]
    g = fs.replace(f"{K}/regime/", "/data/tk0196/regime/")
    if not os.path.exists(g): say(f"спот/фьюч {m}: нет фьючерсного {g}"); continue
    a = {r["minute_ms"]: r["btc_ret_4h_bps"] for r in csv.DictReader(open(fs))}
    b = {r["minute_ms"]: r["btc_ret_4h_bps"] for r in csv.DictReader(open(g))}
    d = [abs(float(a[k]) - float(b[k])) for k in a if k in b and a[k] != "" and b[k] != ""]
    say(f"спот/фьюч {m} {os.path.basename(fs)}: общих минут {len(d)}, средняя |разница| btc4h {statistics.mean(d) if d else float('nan'):.2f} bps")
PY
# ---- подходы против касаний по месяцам (сутки × символ)
python3 - "$K" "$R" <<'PY'
import csv, sys
K, R = sys.argv[1], sys.argv[2]
out = open(R, "a")
def lines(p):
    try:
        with open(p, "rb") as f: return max(sum(1 for _ in f) - 1, 0)
    except OSError: return -1
agg = {}
for r in csv.DictReader(open(f"{K}/b1-symdays.csv")):
    m, d, s = r["month"], r["day"], r["symbol"]
    base = f"{K}/jrn/{d}/wt/sub/D20/{d}"
    t, a = lines(f"{base}/touches-{s}.csv"), lines(f"{base}/approaches-{s}.csv")
    x = agg.setdefault(m, [0, 0, 0, 0, 0, 0]); x[0] += 1
    if t < 0: x[5] += 1; continue
    x[1] += t > 0; x[2] += a > 0; x[3] += t; x[4] += a
for m in "jan feb mar apr may jun jul aug sep".split():
    x = agg.get(m, [0] * 6)
    s = f"подходы {m}: монето-суток {x[0]}, без файла {x[5]}, с касаниями {x[1]}, с подходами {x[2]}, строк касаний {x[3]}, строк подходов {x[4]}"
    print(s); out.write(s + "\n")
PY
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
touch "$F/done"
