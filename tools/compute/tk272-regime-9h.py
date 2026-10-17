#!/usr/bin/env python3
"""TK-272 (Г-10): дописать btc_ret_9h_bps к готовым файлам режима месяца (уже с btc_ret_6h…24h_bps), исходные не трогать.
Формула и колонки — как tools/compute/regime.py (BTC_9H, ret_bps по ref-BTCUSDT-1m.csv, колонка в самом конце).
    tk272-regime-9h.py <study/regime месяца> <выходной каталог>"""
import csv, os, sys, shutil
import importlib.util
spec = importlib.util.spec_from_file_location("regime", os.path.join(os.path.dirname(os.path.abspath(__file__)), "regime.py"))
rg = importlib.util.module_from_spec(spec); spec.loader.exec_module(rg)
src, dst = sys.argv[1], sys.argv[2]
os.makedirs(dst, exist_ok=True)
ref = rg.load_minutes(os.path.join(src, "ref-BTCUSDT-1m.csv"), "close")
names = [f"btc_ret_{k}_bps" for k in rg.BTC_9H]
n = 0
for fn in sorted(os.listdir(src)):
    p, q = os.path.join(src, fn), os.path.join(dst, fn)
    if not (fn[:2] == "20" and fn.endswith(".csv") and len(fn) == 14):
        if not os.path.exists(q): os.symlink(os.path.realpath(p), q)
        continue
    with open(p, encoding="utf-8", newline="") as f, open(q + ".tmp", "w", encoding="utf-8", newline="") as g:
        r, w = csv.reader(f), csv.writer(g)
        h = next(r)
        if names[0] in h: shutil.copy(p, q + ".tmp"); os.replace(q + ".tmp", q); continue
        if "btc_ret_24h_bps" not in h: sys.exit(f"{p}: нет btc_ret_24h_bps — сначала tk196-regime-long.py")
        w.writerow(h + names)
        for row in r:
            m = int(row[0])
            w.writerow(row + [rg.fmt(rg.ret_bps(ref, m, b)) for b in rg.BTC_9H.values()])
    os.replace(q + ".tmp", q); n += 1
print(f"{src}: {n} файлов -> {dst}")
