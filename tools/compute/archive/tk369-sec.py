#!/usr/bin/env python3
"""TK-369 / G-016: посекундный ряд сделок Bybit из публичного архива (public.bybit.com) для банка гипотез P-25.
usage: tk369-sec.py <outdir> <symbols.txt> [P=4] [days=2026-10-15..2026-10-22]
Строка выхода (TSV, по секундам UTC с хотя бы одной сделкой): sec  last  first  n  buy_qty  sell_qty  (цена — как в файле, qty — в монетах).
Файл <outdir>/<mk>/<SYM>-<день>.tsv; 404 — запись в <outdir>/missing.log; готово — <outdir>/DONE.json. Потоков скачивания <= 4 (бан по IP при 32, TK-366).
mk = fut (trading/<SYM>/<SYM><день>.csv.gz, ts — секунды с дробью) | spot (spot/<SYM>/<SYM>_<день>.csv.gz, ts — мс)."""
import sys, os, gzip, json, time, urllib.request, urllib.error
from multiprocessing import Pool

OUT, SYMS = sys.argv[1:3]
P = int(sys.argv[3]) if len(sys.argv) > 3 else 4
DAYS = [f"2026-10-{d}" for d in range(15, 23)]
BASE = "https://public.bybit.com"

def one(a):
    mk, sym, day = a
    f = os.path.join(OUT, mk, f"{sym}-{day}.tsv")
    if os.path.exists(f):
        return (a, "have", 0)
    url = f"{BASE}/trading/{sym}/{sym}{day}.csv.gz" if mk == "fut" else f"{BASE}/spot/{sym}/{sym}_{day}.csv.gz"
    raw = None
    for i in range(5):
        try:
            raw = urllib.request.urlopen(urllib.request.Request(url, headers={"user-agent": "Mozilla/5.0"}), timeout=120).read()
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return (a, "404", 0)
            time.sleep(3 * (i + 1))
        except Exception:
            time.sleep(3 * (i + 1))
    if raw is None:
        return (a, "fail", 0)
    agg = {}  # sec -> [last, first, n, buy, sell]
    n_rows = 0
    first = True
    for ln in gzip.decompress(raw).decode().splitlines():
        if first:
            first = False
            continue
        c = ln.split(",")
        if mk == "fut":
            t = float(c[0]); side = c[2]; q = float(c[3]); p = c[4]
        else:
            t = float(c[1]) / 1000.0; p = c[2]; q = float(c[3]); side = c[4]
        s = int(t)
        a_ = agg.get(s)
        if a_ is None:
            a_ = agg[s] = [p, p, 0, 0.0, 0.0]
        a_[0] = p; a_[2] += 1
        if side[0] in "Bb":
            a_[3] += q
        else:
            a_[4] += q
        n_rows += 1
    os.makedirs(os.path.dirname(f), exist_ok=True)
    tmp = f + ".part"
    with open(tmp, "w", newline="\n") as w:
        for s in sorted(agg):
            v = agg[s]
            w.write(f"{s}\t{v[0]}\t{v[1]}\t{v[2]}\t{v[3]:.10g}\t{v[4]:.10g}\n")
    os.replace(tmp, f)
    return (a, "ok", n_rows)

if __name__ == "__main__":
    syms = [x.strip() for x in open(SYMS) if x.strip()]
    jobs = [(mk, s, d) for s in syms for mk in ("fut", "spot") for d in DAYS]
    os.makedirs(OUT, exist_ok=True)
    t0 = time.time()
    st = {}
    with Pool(P) as pool, open(os.path.join(OUT, "missing.log"), "a") as ml:
        for i, (a, s, n) in enumerate(pool.imap_unordered(one, jobs, chunksize=1)):
            st[s] = st.get(s, 0) + 1
            if s in ("404", "fail"):
                ml.write(f"{s}\t{a[0]}\t{a[1]}\t{a[2]}\n"); ml.flush()
            if i % 100 == 0:
                print(i, len(jobs), st, int(time.time() - t0), flush=True)
    json.dump({"jobs": len(jobs), "status": st, "secs": int(time.time() - t0)}, open(os.path.join(OUT, "DONE.json"), "w"))
    print("DONE", st, flush=True)
