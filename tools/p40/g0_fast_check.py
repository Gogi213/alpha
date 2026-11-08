"""Сверка g0_fast.one с g0_chft_generic.one на тех же файлах: python g0_fast_check.py f1.parquet ... (родитель файла = каталог биржи)."""
import sys, time, json
import g0_fast, g0_chft_generic as old
ok = True
for p in sys.argv[1:]:
    t0 = time.time(); a = old.one(p); t1 = time.time(); b = g0_fast.one(p); t2 = time.time()
    same = json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    ok &= same
    print(p.split("/")[-1], "rows", a.get("rows"), "old %.1fs new %.1fs" % (t1 - t0, t2 - t1), "SAME" if same else "DIFF")
    if not same: print(" old", a); print(" new", b)
sys.exit(0 if ok else 1)
