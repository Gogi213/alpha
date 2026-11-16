"""Prod collector view of a full hour (kernel arrival stamps, all trade shards of one exchange pooled): per-window lag and the largest
arrival gap + burst after it. Usage: prod_hour_profile.py <root> <prefix> <epoch_hour> [<epoch_hour>...]  (lag = rx_ms - E_ms)."""
import sys, glob, re, os, subprocess, datetime
import numpy as np
root, prefix = sys.argv[1], sys.argv[2]
hours = [int(x) for x in sys.argv[3:]]
t_lo, t_hi = min(hours) - 90, max(hours) + 150
days = sorted({datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%d") for t in (t_lo, t_hi)})
rx_all, lag_all = [], []
for day in days:
    for f in sorted(glob.glob(f"{root}/{day}/{prefix}*.tsv.zst")):
        m = re.search(r"-(\d+)\.tsv\.zst$", f)
        if not m or int(m.group(1)) > t_hi or os.path.getmtime(f) < t_lo:
            continue
        p = subprocess.Popen(["zstd", "-dc", f], stdout=subprocess.PIPE, bufsize=1 << 22)
        rx = 0
        for line in p.stdout:
            a = line.split(b"\t", 4)
            try:
                rx += int(a[1])  # every line type carries dns (rx delta) in column 2, also the short `g` rows
            except (ValueError, IndexError):
                continue
            if len(a) >= 4 and a[0] in (b"t", b"b") and t_lo * 10**9 <= rx <= t_hi * 10**9:
                rx_all.append(rx); lag_all.append(int(a[2]))
        p.wait()
rx = np.array(rx_all, dtype="int64"); lag = np.array(lag_all, dtype="float64")
o = np.argsort(rx); rx, lag = rx[o], lag[o]
print(f"rows {len(rx)}")
for H in hours:
    print("== hour", datetime.datetime.fromtimestamp(H, datetime.timezone.utc).strftime("%m-%d %H:00"))
    d = np.diff(rx) / 1e6
    m = (rx[:-1] >= (H - 30) * 10**9) & (rx[:-1] < (H + 90) * 10**9)
    if m.any():
        j = np.argmax(np.where(m, d, -1))
        burst = int(((rx >= rx[j + 1]) & (rx < rx[j + 1] + 50_000_000)).sum())
        print(f"  largest arrival gap in -30..+90 s: {d[j]:.0f} ms starting at {(rx[j]/1e9 - H):+.3f} s rel. hour; rows in first 50 ms after: {burst}; median gap {np.median(d[m]):.1f} ms")
    bins = [(a, a + 1) for a in range(-15, 21)] if os.environ.get('FINE') else [(-60,-30),(-30,-10),(-10,0),(0,1),(1,2),(2,3),(3,5),(5,10),(10,20),(20,30),(30,60),(60,120)]
    for a, b in bins:
        k = (rx >= (H + a) * 10**9) & (rx < (H + b) * 10**9)
        if k.any():
            x = lag[k]
            print(f"  {a:+4d}..{b:+4d} s n={k.sum():6d} lag p50 {np.percentile(x,50):7.1f} p99 {np.percentile(x,99):7.1f} max {x.max():7.1f}")
