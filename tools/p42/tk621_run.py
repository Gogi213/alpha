#!/usr/bin/env python3
"""TK-621: L_base and S_base (§13.28 п. 2, verbatim) on a symbol universe, three windows wa/wb/wc, engine as TK-599 (alpha-master a112684a).
  python3 tk621_run.py <tag> <symbols.txt> <t24 in M$ or 0> [root=/data/p42-tk621] [trees=<root>]
Output: <root>/out/<tag>/<window>/<vid>/{long,short}-trades.csv ... ; log <root>/out/<tag>/<window>.log; <root>/out/<tag>/DONE (rc per window).
`t24` = trailing 24 h quote turnover of closed minutes at signal time >= t24 M$ (engine key, 0 = off); no other key changes.
Run under systemd-run on srv; never touches /data/p42-tk599/out."""
import os
import subprocess
import sys
import time

BIN = "/data/p42-tk599/bin/alpha-master"
STORE = "/data/p42-tk599/tick-store"
T0 = {"wa": "2026-06-17", "wb": "2026-07-25", "wc": "2026-09-01"}
BASE = {
    "L_base": "lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 rf=0.2 rs=20",
    "S_base": "lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 tt=20 tb=0.1 rf=0.2 rs=2 bm=1 e=0.15 e2=0.05",
}
tag, symf, t24 = sys.argv[1], sys.argv[2], sys.argv[3]
root = sys.argv[4] if len(sys.argv) > 4 else "/data/p42-tk621"
trees = sys.argv[5] if len(sys.argv) > 5 else root
out = f"{root}/out/{tag}"
os.makedirs(out, exist_ok=True)
vfile = f"{out}/variants.txt"
with open(vfile, "w") as f:
    f.write(f"# TK-621 tag={tag} symbols={symf} t24={t24}\n")
    for k, v in BASE.items():
        f.write(f"{k} {v}" + (f" t24={t24}" if float(t24) > 0 else "") + "\n")
syms = open(symf, encoding="utf-8").read().strip()
rcs = {}
for w, t0 in T0.items():
    cmd = [BIN, "breakout", "tick", "--cache", f"{trees}/{w}/cache", "--trades", f"{trees}/{w}/aggtrades", "--variants", vfile,
           "--symbols", syms, "--out", f"{out}/{w}", "--threads", "6", "--tick-store", STORE, "--tick-store-cap-gb", "3",
           "--tick-store-min-free-gb", "5"]
    t = time.time()
    with open(f"{out}/{w}.log", "w") as lg:
        rcs[w] = subprocess.run(cmd, env={**os.environ, "P42_T0": t0}, stdout=lg, stderr=subprocess.STDOUT).returncode
    print(tag, w, "rc", rcs[w], "sec", round(time.time() - t), flush=True)
    if rcs[w]:
        break
open(f"{out}/DONE", "w").write(repr(rcs) + "\n")
