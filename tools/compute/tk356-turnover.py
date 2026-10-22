#!/usr/bin/env python3
"""TK-356 (G-015): суточный оборот монеты ($) и шаг цены по (режим, монета, сутки) из ленты `lob trades`.
Вход: CSV признаков (tk327 fut/spot, p23 fut-B) -> уникальные (mode,sym,day). Выход: mode,sym,day,turn_usd,n_trades,tick_inst,tick_bin,err.
Использование: tk356-turnover.py OUT --workers N IN1.csv [IN2.csv ...]"""
import csv, os, struct, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
BIN = "/opt/alpha-compute/bin/alpha-tk312-pgo"
MONS = {"01": "jan", "02": "feb", "03": "mar", "04": "apr", "05": "may", "06": "jun", "07": "jul", "08": "aug", "09": "sep"}
OUT = sys.argv[1]
W = int(sys.argv[3]); ins = sys.argv[4:]
keys = set()
for p in ins:
    with open(p, newline="") as f:
        for r in csv.DictReader(f):
            keys.add((r["mode"], r["sym"], r["day"]))

def paths(mode, day):
    mo = MONS[day[5:7]]
    if mode == "spot":
        return f"/data/tk0319/e-{mo}/study/root-{day}", "/data/tk0319/instruments.csv"
    e = f"/data/tk046/{mo}/home/alpha/epochs/e-{mo}"
    return f"{e}/study/root-{day}", f"{e}/study/root-{day}/instruments.csv"

_inst = {}
def tick_inst(path, sym):
    if path not in _inst:
        with open(path, newline="") as f:
            _inst[path] = {r["symbol"]: r["tick_size"] for r in csv.DictReader(l for l in f if not l.startswith("#"))}
    return _inst[path].get(sym, "")

def one(k):
    mode, sym, day = k
    root, inst = paths(mode, day)
    tmp = tempfile.mktemp(prefix="tk356-", suffix=".csv", dir=os.environ.get("TK356_TMP", "/tmp"))
    try:
        with open(f"{root}/{sym}-{day}.binlog", "rb") as f:
            h = f.read(21)
        t9, s9 = struct.unpack("<qq", h[5:21]); lot = s9 / 1e9
        subprocess.run([BIN, "lob", "trades", "--root", root, "--symbol", sym, "--day", day, "--out", tmp],
                       check=True, capture_output=True, text=True)
        tot = 0.0; n = 0
        with open(tmp, newline="") as f:
            rd = csv.reader(f); hd = {c: i for i, c in enumerate(next(rd))}
            ip, iq = hd["price_usd"], hd["qty_lots"]
            for r in rd:
                tot += float(r[ip]) * int(r[iq]) * lot; n += 1
        return (mode, sym, day, round(tot, 1), n, tick_inst(inst, sym), t9 / 1e9, "")
    except Exception as e:
        return (mode, sym, day, "", "", "", "", f"{type(e).__name__}:{str(e)[:80]}")
    finally:
        if os.path.exists(tmp): os.remove(tmp)

ks = sorted(keys); print(len(ks), "групп", flush=True)
with open(OUT + ".tmp", "w", newline="") as f, ThreadPoolExecutor(W) as ex:
    w = csv.writer(f); w.writerow("mode sym day turn_usd n_trades tick_inst tick_bin err".split())
    for i, row in enumerate(ex.map(one, ks)):
        w.writerow(row)
        if i % 100 == 0: print(i, flush=True); f.flush()
os.replace(OUT + ".tmp", OUT); print("done")
