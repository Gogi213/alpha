#!/usr/bin/env python3
"""TK-212: слить выходы двух пачек bounce-grid (std + tape) в один каталог суток. merge-packs.py <std> <tape> <out>
Файлы только в одной пачке — копия; общие csv (forms/signals/rounds) — строки std после заголовка tape (шире), заголовок
второй пачки отбрасывается; строки std-клеток без хвостовых tape-колонок (tape_press_lots, cxl_lots, chase_wait_ms) короче
заголовка. manifest.txt — std, tape — manifest-tape.txt."""
import os, shutil, sys
std, tape, out = sys.argv[1:4]
def files(r):
    return {os.path.relpath(os.path.join(p, f), r): os.path.join(p, f) for p, _, fs in os.walk(r) for f in fs} if os.path.isdir(r) else {}
a, b = files(std), files(tape)
for rel in sorted(set(a) | set(b)):
    dst = os.path.join(out, rel); os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.basename(rel) == "manifest.txt":
        shutil.copy(a.get(rel) or b[rel], dst)
        if rel in a and rel in b: shutil.copy(b[rel], os.path.join(os.path.dirname(dst), "manifest-tape.txt"))
    elif rel in a and rel in b:
        if not rel.endswith(".csv"): sys.exit("общий не-csv: " + rel)
        la = open(a[rel], encoding="utf-8", newline="").read().splitlines(True); lb = open(b[rel], encoding="utf-8", newline="").read().splitlines(True)
        body = [l for l in la if not l.startswith("#")][1:]
        with open(dst, "w", encoding="utf-8", newline="") as f: f.writelines(lb + body)
    else: shutil.copy(a.get(rel) or b[rel], dst)
