#!/usr/bin/env python3
"""bxfacts: trade id continuity per symbol (BingX trade ids are contiguous per symbol): ids missing inside the seen range, 50 ids trimmed at each edge.
  tids.py <day dir> <run id>
"""
import subprocess, sys, collections, json, glob, os
res = {}
for path in sorted(glob.glob(sys.argv[1] + "/trade*-" + sys.argv[2] + ".tsv.zst")):
    sh = os.path.basename(path).split("-")[0]
    p = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    last = {}; ids = collections.defaultdict(list); n = 0
    for raw in p.stdout:
        ln = raw.decode("utf-8", "replace").rstrip("\n")
        if not ln or ln[0] != "t": continue
        f = ln.split("\t")
        if len(f) < 8: continue
        try:
            s = f[4]; a = last.get(s, 0) + int(f[5]); last[s] = a + 1
            ids[s].append((a, int(f[1])))
            n += 1
        except Exception: continue
    miss = 0; syms_miss = 0; span = 0; ex = []
    for s, l in ids.items():
        v = sorted({x[0] for x in l})
        if len(v) < 120: continue
        v = v[50:-50]
        rng = v[-1] - v[0] + 1; span += rng; m = rng - len(v)
        if m > 0: syms_miss += 1; miss += m; ex.append((s, m))
    res[sh] = {"rows": n, "symbols": len(ids), "id_span": span, "missing": miss, "symbols_with_missing": syms_miss, "ex": sorted(ex, key=lambda x: -x[1])[:3]}
print(json.dumps(res))
