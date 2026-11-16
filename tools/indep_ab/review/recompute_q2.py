#!/usr/bin/env python3
"""TK-535 review: independent recompute of Q2 (:00 spike) from raw host logs, sar and Binance raw rows.

python3 recompute_q2.py > q2.txt   (srv, niced)
"""
import re, subprocess
import numpy as np
import pandas as pd

H = {17: 1794848400, 18: 1794852000, 19: 1794855600}  # 2026-11-16 hh:00:00 UTC
DAY0 = 1794787200  # 2026-11-16 00:00:00 UTC

# ---- (a) mpstat 1 Hz, all CPUs: steal and guest demand (usr+nice+sys+irq+soft+iowait) per second around each hour
rows = []
for line in open("/data/indep-ab/host/mpstat.log"):
    f = line.split()
    if len(f) == 12 and f[1] == "all":
        hh, mm, ss = map(int, f[0].split(":"))
        rows.append((DAY0 + hh * 3600 + mm * 60 + ss, *map(float, f[2:])))
mp = pd.DataFrame(rows, columns=["t", "usr", "nice", "sys", "iow", "irq", "soft", "steal", "guest", "gnice", "idle"]).drop_duplicates("t")
mp["busy"] = mp.usr + mp.nice + mp.sys + mp.irq + mp.soft
mp = mp.set_index("t")
print("== (a) mpstat all-CPU per second: steal / busy(usr+nice+sys+irq+soft) / idle")
for h, t in H.items():
    bg = mp.loc[t - 300:t - 31]
    print(f"{h}:00 background -300..-31 s: steal mean {bg.steal.mean():.2f} busy mean {bg.busy.mean():.1f} idle {bg.idle.mean():.1f}")
    for s in range(-2, 9):
        r = mp.loc[t + s] if t + s in mp.index else None
        print(f"  {s:+d}: " + ("missing" if r is None else f"steal {r.steal:5.1f} busy {r.busy:5.1f} idle {r.idle:5.1f}"))
# share of seconds with steal >= 10 % at :00:01-:00:06 vs elsewhere, whole window
mp2 = mp.reset_index()
mp2["sec_of_hour"] = mp2.t % 3600
at = mp2[(mp2.sec_of_hour >= 1) & (mp2.sec_of_hour <= 6)]
print(f"steal mean at :00:01-06 {at.steal.mean():.1f} (n={len(at)}) vs rest {mp2[~mp2.index.isin(at.index)].steal.mean():.2f}")
miss = [t for t in range(int(mp.index.min()), int(mp.index.max())) if t not in mp.index]
print(f"mpstat missing seconds: {len(miss)}; within 0..+10 of an hour: {[ (t % 3600) for t in miss if t % 3600 <= 10]}")

# ---- (b) sar 10 s, all hours of sa13-sa16: bucket containing :00:00 vs the rest; steal vs busy in the SAME bucket
print("== (b) sar")
out = []
for d in ("sa13", "sa14", "sa15", "sa16"):
    txt = subprocess.run(["sadf", "-d", f"/var/log/sysstat/{d}", "--", "-u"], capture_output=True, text=True).stdout
    for line in txt.splitlines():
        if line.startswith("#"):
            continue
        f = line.split(";")
        if len(f) < 10 or f[3] != "-1":
            continue
        ts = pd.Timestamp(f[2].replace(" UTC", ""), tz="UTC").value // 10**9
        iv = int(f[1])
        user, nice, sys_, iow, steal, idle = map(float, f[4:10])
        out.append((ts - iv, ts, iv, user + nice + sys_, steal, idle))
sar = pd.DataFrame(out, columns=["t0", "t1", "iv", "busy", "steal", "idle"]).drop_duplicates("t1").sort_values("t1")
sar["has00"] = (sar.t1 % 3600 > 0) & (sar.t1 % 3600 <= sar.iv)  # bucket (t0, t1] covers the seconds right after :00:00
s00 = sar[sar.has00 & (sar.iv >= 5)]
rest = sar[~sar.has00]
print(f"buckets with :00:00 (iv>=5 s): n={len(s00)} steal mean {s00.steal.mean():.2f} median {s00.steal.median():.2f}; other buckets mean {rest.steal.mean():.2f} median {rest.steal.median():.2f}")
mid = s00[(s00.t1 % 86400) > 60]
print(f"  excluding 00:00 UTC (sysstat rotate / dpkg backup timers): n={len(mid)} mean {mid.steal.mean():.2f}")
# 5-min marks other than the hour
m5 = sar[(~sar.has00) & (sar.t1 % 300 > 0) & (sar.t1 % 300 <= sar.iv) & (sar.iv >= 5)]
print(f"5-min marks (not :00): n={len(m5)} steal mean {m5.steal.mean():.2f} median {m5.steal.median():.2f}")
# same-bucket demand vs steal, and prior-5-min load vs steal
prev = []
for _, r in s00.iterrows():
    p = sar[(sar.t1 > r.t0 - 300) & (sar.t1 <= r.t0)]
    prev.append(p.busy.mean() if len(p) else np.nan)
s00 = s00.assign(prev_busy=prev)
print(f"rank corr steal(:00 bucket) vs busy 5 min before: {s00.steal.corr(s00.prev_busy, method='spearman'):.3f}")
print(f"rank corr steal(:00 bucket) vs busy in same bucket: {s00.steal.corr(s00.busy, method='spearman'):.3f}")
print(f"busy in :00 bucket mean {s00.busy.mean():.1f} vs 5 min before mean {s00.prev_busy.mean():.1f}")
terc = pd.qcut(s00.prev_busy, 3, labels=["low", "mid", "high"])
print("steal by tercile of prior load:", s00.groupby(terc, observed=True).steal.agg(["count", "mean"]).round(2).to_dict())
# steal in :00 bucket by day: does it exist on sa13 (before the 16.11 hold_* jobs and before clients)?
s00 = s00.assign(day=pd.to_datetime(s00.t1, unit="s").dt.day)
print("steal(:00 bucket) by day:", s00.groupby("day").steal.agg(["count", "mean", "median"]).round(2).to_dict("index"))

# ---- (c) Binance exchange-internal delay E - T (aggTrade) and kernel lag k - E, independent client, per second
print("== (c) Binance independent client: k-E lag p50 and E-T (exchange internal) p50/p99 per second around hours")
bn = pd.read_csv("/data/indep-ab/run1bn/bntrade0.tsv", sep="\t", header=None, names=["kind", "k", "u", "sym", "id", "T", "E"],
                 dtype={"k": "Int64"})
bn["k"] = bn["k"].fillna(bn["u"]).astype("int64")
bn["sec"] = bn.k // 10**9
bn["lag"] = bn.k / 1e6 - bn.E
bn["ET"] = bn.E - bn["T"]
bb = pd.read_csv("/data/indep-ab/run1bn/bnbook0.tsv", sep="\t", header=None, names=["kind", "k", "u", "sym", "id", "T", "E"],
                 dtype={"k": "Int64"})
bb["k"] = bb["k"].fillna(bb["u"]).astype("int64")
bb["sec"] = bb.k // 10**9
bb["lag"] = bb.k / 1e6 - bb.E
bb["ET"] = bb.E - bb["T"]
for name, df in (("aggTrade", bn), ("bookTicker", bb)):
    for h, t in H.items():
        bg = df[(df.sec >= t - 300) & (df.sec < t - 30)]
        print(f"{name} {h}:00 bg lag p50 {bg.lag.median():.0f} ET p50 {bg.ET.median():.0f} p99 {bg.ET.quantile(.99):.0f} msgs/s {len(bg) / 270:.0f}")
        for s in range(-1, 6):
            x = df[df.sec == t + s]
            if len(x):
                print(f"  {s:+d}: n {len(x):5d} lag p50 {x.lag.median():6.0f} p99 {x.lag.quantile(.99):6.0f} | E-T p50 {x.ET.median():5.0f} p99 {x.ET.quantile(.99):5.0f}")
