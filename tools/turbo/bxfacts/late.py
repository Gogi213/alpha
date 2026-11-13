#!/usr/bin/env python3
"""bxfacts: late (lag > N ms) records and `c` rows of the files `*-<run id>.tsv.zst` of a data day; JSON per shard. Run on srv under systemd-run (nice 19, ionice idle).
  late.py <day dir> <late ms> <out.json> <run id>
"""
import subprocess, sys, json, glob, os
LATE = int(sys.argv[2]) if len(sys.argv) > 2 else 2000
out = {}
for path in sorted(glob.glob(sys.argv[1] + "/*-" + (sys.argv[4] if len(sys.argv) > 4 else "1794577010") + ".tsv.zst")):
    shard = os.path.basename(path).split("-")[0]
    p = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    rx = 0; late = []; cs = []; n = 0
    for raw in p.stdout:
        try:
            ln = raw.decode("utf-8", "replace").rstrip("\n")
            if not ln: continue
            f = ln.split("\t", 2) if ln[0] == "r" else ln.split("\t")
            rx += int(f[1]); n += 1
            t = f[0]
            if t in ("b", "m", "t"):
                lag = int(f[2])
                if lag > LATE:
                    sym = f[4] if t in ("b", "t") else f[3]
                    late.append([rx // 1_000_000, t, sym, lag])
            elif t == "c":
                cs.append([rx // 1_000_000, f[2], f[3] if len(f) > 3 else ""])
        except Exception:
            pass
    out[shard] = {"n": n, "late": late, "c": cs}
json.dump(out, open(sys.argv[3] if len(sys.argv) > 3 else "/tmp/bxfix/late.json", "w"))
print({k: (v["n"], len(v["late"])) for k, v in out.items()})
