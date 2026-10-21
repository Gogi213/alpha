#!/usr/bin/env python3
"""TK-312: список исключений спот-бинлогов (месяц, монета, причина) + перенос бинлогов монет с размером > i64 e9 в excluded/.
Читает OUT/import.tsv (sym day rc …) и OUT/import-verify.tsv (месяц sym rc status), OUT/e-М/verify-SYM.log (итоговая строка).
Пишет CSV в argv[1]; с --move переносит все файлы монет i64e9 (PEPE/BONK/SHIB/MOG…) из e-М/ в OUT-excluded/e-М/."""
import os, re, sys

OUT = "/srv/alpha-sda/binlog-spot"
EXC = OUT + "-excluded"
csv_path = sys.argv[1]
move = "--move" in sys.argv

bad_coins = sorted({l.split("\t")[0] for l in open(f"{OUT}/import.tsv") if l.split("\t")[2] == "1"})
rows = []
tot = {}
for l in open(f"{OUT}/import-verify.tsv"):
    m, sym, rc, st = l.rstrip("\n").split("\t")[:4]
    tot[(m, sym)] = st
months = sorted({m for m, _ in tot})
for m in months:
    for sym in bad_coins:
        # все пары этих монет (в т.ч. после переимпорта — fail), причина — размер
        if os.path.isdir(f"{OUT}/e-{m}") and any(f.startswith(sym + "-") for f in os.listdir(f"{OUT}/e-{m}")):
            rows.append((m, sym, "i64e9", ""))
for (m, sym), st in sorted(tot.items()):
    if st.strip() != "ok" and sym not in bad_coins:
        last = [x for x in open(f"{OUT}/e-{m}/verify-{sym}.log", errors="replace") if x.startswith("verify: files=")][-1]
        g = lambda k: int(re.search(k + r"=(\d+)", last).group(1))
        rows.append((m, sym, "far-ppm", f"{g('violations') * 1_000_000 // g('trades')}"))
with open(csv_path, "w") as f:
    f.write("month,symbol,reason,ppm\n")
    for r in rows:
        f.write(",".join(r) + "\n")
print(f"монет i64e9: {bad_coins}; строк исключений: {len(rows)} (i64e9 {sum(r[2]=='i64e9' for r in rows)}, far-ppm {sum(r[2]=='far-ppm' for r in rows)})")
if move:
    n = 0
    for m in months:
        d = f"{OUT}/e-{m}"
        if not os.path.isdir(d):
            continue
        os.makedirs(f"{EXC}/e-{m}", exist_ok=True)
        for fn in sorted(os.listdir(d)):
            if any(fn.startswith(s + "-") or fn.startswith("verify-" + s + ".") for s in bad_coins):
                os.rename(f"{d}/{fn}", f"{EXC}/e-{m}/{fn}")
                n += 1
    print(f"перенесено файлов: {n} → {EXC}")
