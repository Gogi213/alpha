#!/usr/bin/env python3
"""bxfacts: records per second by arrival (rx) and by exchange time (E) for one shard file: a rx-hole with a complete E-curve = delayed data; an E-hole = lost data.
  edens.py <shard.tsv.zst> <epoch s from> <epoch s to>
"""
import subprocess, sys, collections
path, t_from, t_to = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
p = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
rx = 0; byrx = collections.Counter(); byE = collections.Counter(); late = collections.Counter()
for raw in p.stdout:
    ln = raw.decode("utf-8", "replace").rstrip("\n")
    if not ln: continue
    f = ln.split("\t", 2) if ln[0] == "r" else ln.split("\t")
    try: rx += int(f[1])
    except Exception: continue
    if f[0] not in ("b", "m", "t"): continue
    rms = rx // 1_000_000
    if rms < t_from * 1000 - 30000: continue
    if rms > t_to * 1000 + 60000: break
    lag = int(f[2]); E = rms - lag
    byrx[rms // 1000] += 1; byE[E // 1000] += 1
p.kill()
print("sec(rel)  n_by_rx  n_by_E")
for s in range(t_from, t_to):
    print(f"{s - t_from:4d} {byrx.get(s,0):7d} {byE.get(s,0):7d}")
