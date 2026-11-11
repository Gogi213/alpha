import subprocess, glob, sys, collections
sys.path.insert(0, "/root/bnws")
from wscore.codec import decode
T0 = 1794432290
ev = collections.defaultdict(list)
for f in sorted(glob.glob("/data/bnws/2026-11-11/book0*-%d.tsv.zst" % T0) + glob.glob("/data/bnws/2026-11-11/trade0*-%d.tsv.zst" % T0)):
    p = subprocess.run(["zstdcat", f], capture_output=True)
    for r in decode(p.stdout.decode(errors="ignore").split("\n")[:-1] if p.stdout else []):
        if r["t"] in ("b", "t"):
            ev[r["s"]].append(r["rx_ns"] // 1_000_000_000)
end = max(max(v) for v in ev.values())
start = min(min(v) for v in ev.values())
out = []
for s, v in ev.items():
    v.sort()
    pts = [start] + v + [end]
    gaps = [(a, b - a) for a, b in zip(pts, pts[1:]) if b - a > 60]
    if gaps:
        out.append((s, len(gaps), max(g for _, g in gaps), [a for a, _ in gaps][:3]))
print("symbols", len(ev), "window", start, end, "silent>60s:", len(out))
for r in sorted(out, key=lambda x: -x[2]): print(r)
