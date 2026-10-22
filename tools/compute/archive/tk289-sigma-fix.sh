#!/usr/bin/env bash
# TK-289 П-18 ось 8, правка sig2 (tk289-sigma-pad.sh): у части монет в источниках (tk046/tk200) нет последних суток ПРЕДЫДУЩЕГО месяца → первые 40 минут месяца в sig2 считаются по окну короче, чем у эпохи
#   (check240: jan 46 монет, feb 3, mar 1, may 3, jun 1, jul 1, aug 2, sep 4; расхождение — ровно 40 первых строк, остальное байт в байт). Причина найдена 21.10 (tk289-sigdiff: первое расхождение = первая строка sig2).
#   Здесь: для монет из sig2/check240-<мес>.diff докачать сутки перед месяцем (ref-klines.py, Bybit REST — те же свечи, что у эпохи) в sig2/fill/<мес>, пересобрать свечи ТОЛЬКО этих монет в sig2/klines/<мес>,
#   пересчитать окна 240 60 90 120 180 360 480 720 960 только для них (sigma-table.py --symbols, остальные файлы не трогаются), затем сверка 240 с эпохой → sig2/check240-fix.txt (общие минуты, значения).
#   Маркер конца: sig2/fixdone (rc 0) или sig2/fixfail. Повтор — дельта (метка fill/<мес>.ok).
set -uo pipefail
S=/data/tk0289/sig2; ST=/data/tk0289/sigma-table.py; RK=/data/tk0289/scripts/ref-klines.py
[ -e "$RK" ] || RK=/data/tk0289/ref-klines.py
rm -f "$S/fixdone" "$S/fixfail"; : > "$S/check240-fix.txt"
ms=(jan feb mar apr may jun jul aug sep oct)
for i in 0 1 2 3 4 5 6 7 8; do
  m=${ms[$i]}; prev=${ms[$((i-1))]:-}; next=${ms[$((i+1))]}; [ "$i" = 0 ] && prev=""
  [ -s "$S/check240-$m.diff" ] || { echo "$m: расхождений нет" >> "$S/check240-fix.txt"; continue; }
  E=/data/tk046/$m/home/alpha/epochs/e-$m/study/sigma240
  # только монеты, у которых sig2 начинается позже эпохи (нет суток перед месяцем); хвостовые +1 сут (0G) и прочие байтовые отличия не трогаем
  syms=$(sed -n 's/^Files .*\/sigma-\([A-Z0-9]*\)\.csv and .*/\1/p' "$S/check240-$m.diff" | while read -r s; do
    [ "$(sed -n 2p "$E/sigma-$s.csv" | cut -d, -f1)" = "$(sed -n 2p "$S/sigma240/$m/sigma-$s.csv" | cut -d, -f1)" ] || echo "$s"; done | paste -sd,)
  [ -n "$syms" ] || { echo "$m: монет с поздним началом нет" >> "$S/check240-fix.txt"; continue; }
  echo "$m: править $(echo "$syms" | tr ',' '\n' | wc -l) монет" >> "$S/check240-fix.txt"
  day=$(date -u -d "2026-$(printf %02d $((i+1)))-01 -1 day" +%F)
  F=$S/fill/$m
  if [ ! -e "$F.ok" ]; then
    python3 "$RK" --out-dir "$F" --symbols "$syms" --since "$day" --until "$day" > "$S/fill/$m.log" 2>&1 || { echo "$m: ref-klines rc≠0 ($S/fill/$m.log)" >> "$S/fixfail"; continue; }
    touch "$F.ok"
  fi
  K=$S/klines/$m
  python3 - "$m" "$prev" "$next" "$K" "$syms" "$F" <<'PY' || { echo "$m: склейка свечей rc≠0" >> "$S/fixfail"; continue; }
import sys, os, datetime
m, prev, nxt, out, syms, fill = sys.argv[1:7]
y, mm = 2026, ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct"].index(m) + 1
ms = lambda d: int(datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc).timestamp() * 1000)
lo = ms(datetime.date(y, mm, 1)) - 86400000
hi = ms(datetime.date(y + (mm == 12), mm % 12 + 1, 1)) + 86400000
dirs = []
for mon in (m, prev, nxt):
    if not mon: continue
    dirs += [f"/data/tk046/{mon}/study/klines", f"/data/tk200/klines/{mon}"]
dirs.append(fill)
for s in syms.split(","):
    rows = {}
    for d in dirs:
        p = f"{d}/ref-{s}-1m.csv"
        if not os.path.exists(p): continue
        for l in open(p).read().splitlines()[1:]:
            k = int(l.split(",", 1)[0])
            if lo <= k < hi: rows.setdefault(k, l)
    if not rows: sys.exit(f"{s}: свечей нет")
    with open(f"{out}/ref-{s}-1m.csv", "w") as f:
        f.write("minute_ms,open,high,low,close,volume\n")
        f.writelines(rows[k] + "\n" for k in sorted(rows))
print(m, "пересобрано монет:", len(syms.split(",")))
PY
  for W in 240 60 90 120 180 360 480 720 960; do
    python3 "$ST" --klines "$K" --out-dir "$S/sigma$W/$m" --symbols "$syms" --window-min "$W" > "$S/sigma$W/$m.fix.log" 2>&1 || echo "$m $W: sigma-table rc≠0 ($S/sigma$W/$m.fix.log)" >> "$S/fixfail"
  done
done
python3 /data/tk0289/check240-vals.py >> "$S/check240-fix.txt" || echo "check240-vals rc≠0 (расхождение значений остаётся)" >> "$S/fixfail"
[ -e "$S/fixfail" ] && exit 3
echo ok > "$S/fixdone"
