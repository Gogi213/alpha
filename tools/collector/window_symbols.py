"""TK-532 п. 4: какие символы пула без записей в окне и по каким видам (читает файлы шардов запуска 1794452877 по rx_ns); разовый разбор окна приёмки 03:08:32–03:11:32 UTC."""
import glob, os, sys, io, json
sys.path.insert(0, "/root/bbws")
import zstandard
from collections import defaultdict
from wscore import codec
RUN = 1794452877
t0, t1 = 1794452912000, 1794453092000
def lines(path):
    with open(path, "rb") as f:
        prev = None
        for ln in io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(f, read_across_frames=True), encoding="utf-8", errors="replace", newline="\n"):
            if prev is not None: yield prev
            prev = ln
        if prev is not None and prev.endswith("\n"): yield prev
allsyms = defaultdict(lambda: defaultdict(int))
win = defaultdict(lambda: defaultdict(int))
for p in sorted(glob.glob(f"/data/bbws/2026-11-12/*-{RUN}.tsv.zst")):
    for r in codec.decode(lines(p)):
        if r["t"] in "btm":
            allsyms[r["s"]][r["t"]] += 1
            if t0 * 10**6 <= r["rx_ns"] <= t1 * 10**6:
                win[r["s"]][r["t"]] += 1
print("run symbols with any record:", len(allsyms), " with record in window:", len(win))
miss = sorted(set(allsyms) - set(win))
print("no record in window:", miss, [dict(allsyms[s]) for s in miss])
# по видам
for k in "btm":
    print(k, "symbols in window:", sum(1 for s in win if win[s][k]), "all:", sum(1 for s in allsyms if allsyms[s][k]))
kinds_missing = {s: [k for k in "btm" if not win[s][k]] for s in allsyms if s in win and not all(win[s][k] for k in "btm")}
print("symbols with a kind missing in window:", {s: v for s, v in list(kinds_missing.items())[:20]})
json.dump(sorted(allsyms), open("/tmp/pool-run-1794452877.json", "w"))
