#!/usr/bin/env bash
# TK-289 П-18 ось 8, повтор sigma.sh с подкладкой: у готовой sigma240 эпохи свечи месяц ±1 сутки (epoch-box-prep.sh: since FIRST-1d, until LAST+1d), у tk046/tk200 — ровно месяц;
#   sigma.sh поэтому начинала на сутки позже и кончала на сутки раньше (240 расходился, 40 строк разогрева). Здесь свечи месяца склеиваются с соседними месяцами и режутся до [1-е −1 сут; 1-е след. мес +1 сут).
#   Выход /data/tk0289/sig2/sigma<W>/<мес>/sigma-<SYM>.csv ; сверка окна 240 с sigma240 эпохи → sig2/check240.txt ; маркер sig2/done (rc 0) или sig2/fail. Повтор — дельта (метки sigma<W>/<мес>.ok).
set -uo pipefail
S=/data/tk0289/sig2; ST=/data/tk0289/sigma-table.py
mkdir -p "$S"; rm -f "$S/done" "$S/fail"
ms=(jan feb mar apr may jun jul aug sep oct)
for i in 0 1 2 3 4 5 6 7 8; do
  m=${ms[$i]}; prev=${ms[$((i-1))]:-}; next=${ms[$((i+1))]}; [ "$i" = 0 ] && prev=""
  E=/data/tk046/$m/home/alpha/epochs/e-$m/study/sigma240
  syms=$(ls "$E" | sed 's/^sigma-//;s/\.csv$//' | paste -sd,)
  K=$S/klines/$m
  if [ ! -e "$K.ok" ]; then
    rm -rf "$K"; mkdir -p "$K"
    python3 - "$m" "$prev" "$next" "$K" "$syms" <<'PY' || { echo "$m: склейка свечей rc≠0" >> "$S/fail"; continue; }
import sys, os, datetime
m, prev, nxt, out, syms = sys.argv[1:6]
y, mm = 2026, ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct"].index(m) + 1
ms = lambda d: int(datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc).timestamp() * 1000)
lo = ms(datetime.date(y, mm, 1)) - 86400000
hi = ms(datetime.date(y + (mm == 12), mm % 12 + 1, 1)) + 86400000
dirs = []
for mon in (m, prev, nxt):
    if not mon: continue
    dirs += [f"/data/tk046/{mon}/study/klines", f"/data/tk200/klines/{mon}"]
miss = 0
for s in syms.split(","):
    rows = {}
    for d in dirs:
        p = f"{d}/ref-{s}-1m.csv"
        if not os.path.exists(p): continue
        for l in open(p).read().splitlines()[1:]:
            k = int(l.split(",", 1)[0])
            if lo <= k < hi: rows.setdefault(k, l)
    if not rows: miss += 1; continue
    with open(f"{out}/ref-{s}-1m.csv", "w") as f:
        f.write("minute_ms,open,high,low,close,volume\n")
        f.writelines(rows[k] + "\n" for k in sorted(rows))
print(m, "монет без свечей:", miss)
PY
    touch "$K.ok"
  fi
  for W in 240 60 90 120 180 360 480 720 960; do
    out=$S/sigma$W/$m; [ -e "$out.ok" ] && continue
    rm -rf "$out"; mkdir -p "$S/sigma$W"
    if python3 "$ST" --klines "$K" --out-dir "$out" --symbols "$syms" --window-min "$W" > "$out.log" 2>&1; then touch "$out.ok"; else echo "$m $W: sigma-table rc≠0 (см. $out.log)" >> "$S/fail"; fi
  done
  if [ -e "$S/sigma240/$m.ok" ]; then
    if diff -rq "$E" "$S/sigma240/$m" > "$S/check240-$m.diff" 2>&1; then echo "$m: 240 байт в байт с готовой ($(ls "$E" | wc -l) файлов)"; else echo "$m: 240 РАСХОДИТСЯ ($(wc -l < "$S/check240-$m.diff") строк diff)"; fi
  fi
done > "$S/check240.txt"
[ -e "$S/fail" ] && exit 3
echo ok > "$S/done"
