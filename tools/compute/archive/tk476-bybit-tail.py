#!/usr/bin/env python3
"""TK-476: calc докачивает хвост Bybit-дельты за ПК и Софию: монеты с конца списков, claim mkdir (общий с ПК),
готовые (файл в dest) пропускает. Запуск: tk476-bybit-tail.py <out> <list1.txt> [<list2.txt> ...]; env THREADS (48), RPS (100)."""
import importlib.util, os, sys, threading, time
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("K_END", "1793491200000")
here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("hist", max((os.path.join(here, "tk476-bybit-hist.py"), os.path.join(here, "..", "tk476-bybit-hist.py")), key=os.path.exists))
hist = importlib.util.module_from_spec(spec); spec.loader.exec_module(hist)
CLAIMS = "/data/tk0476/claims"
out = sys.argv[1]
args = sys.argv[2:]
threads, rps = int(os.environ.get("THREADS", "48")), float(os.environ.get("RPS", "100"))
syms = []
for f in args:
    syms += [s.strip() for s in open(f) if s.strip()][::-1]  # с конца: ПК и София идут с головы


def work(sym):
    if os.path.exists("%s/bybit-linear/%s.csv.gz" % (out, sym)) or os.path.exists("%s/cov/%s.tsv" % (out, sym)):
        return "skip " + sym
    try:
        os.mkdir("%s/%s" % (CLAIMS, sym))
    except FileExistsError:
        return "skip " + sym
    hist.one(sym, out, rps)
    return "ok " + sym


t0, fails = time.time(), []
with ThreadPoolExecutor(threads) as ex:
    futs = {ex.submit(work, s): s for s in syms}
    for i, fu in enumerate(futs, 1):
        try:
            print(i, len(syms), fu.result(), "%.0f с, запросов %d" % (time.time() - t0, hist.reqs), flush=True)
        except Exception as e:  # noqa: BLE001
            fails.append((futs[fu], str(e))); print("FAIL", futs[fu], e, flush=True)
print("готово, провалов", len(fails), flush=True)
sys.exit(1 if fails else 0)
