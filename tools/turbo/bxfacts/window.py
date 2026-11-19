#!/usr/bin/env python3
"""bxfacts: second-by-second view of one book shard file around an event (records by arrival, burst and first-frames-after-resubscribe composition, silences > 1 s). Run on srv under systemd-run (nice 19, ionice idle).
  window.py <book*.tsv.zst> <epoch s from> <epoch s to>
"""
import subprocess, sys, collections, datetime
path, t_from, t_to = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
p = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE)
rx = 0; rows = []
for raw in p.stdout:
    ln = raw.decode("utf-8", "replace").rstrip("\n")
    if not ln: continue
    f = ln.split("\t", 2) if ln[0] == "r" else ln.split("\t")
    rx += int(f[1])
    if rx > t_to * 1e9: break
    if rx < t_from * 1e9: continue
    if f[0] == "b":
        rms = rx // 1_000_000; lag = int(f[2]); rows.append((rms, rms - lag, f[4], lag))
p.kill()
def rel(ms): return f"{ms/1000 - t_from:.3f}"
per = collections.defaultdict(list)
for r in rows: per[int(r[0]/1000 - t_from)].append(r)
print("sec  n  syms  (rx rel to window start", t_from, ")")
for s in range(0, int(t_to - t_from) + 1):
    l = per.get(s, []); print(f"{s:3d} {len(l):5d} {len({x[2] for x in l}):4d}  lag_med={sorted(x[3] for x in l)[len(l)//2] if l else '-'}")
# burst and gap
rows.sort()
big = [i for i in range(1, len(rows)) if rows[i][0] - rows[i-1][0] > 1000]
print("silences >1 s between consecutive records:", [(rel(rows[i-1][0]), rel(rows[i][0]), (rows[i][0]-rows[i-1][0])/1000) for i in big])
for lo, hi, name in ((7.9, 8.6, "burst"), (13.3, 14.0, "after-resub")):
    b = [r for r in rows if lo <= r[0]/1000 - t_from < hi]
    lags = sorted(r[3] for r in b)
    if not b: print(name, "none"); continue
    h = collections.Counter("<1s" if l < 1000 else "1-5s" if l < 5000 else "5-9s" if l < 9000 else ">=9s" for l in lags)
    print(name, "rx", rel(b[0][0]), "-", rel(b[-1][0]), "n", len(b), "syms", len({r[2] for r in b}), "lag min/med/max", lags[0], lags[len(lags)//2], lags[-1], dict(h), "E range", rel(min(r[1] for r in b)), rel(max(r[1] for r in b)))
