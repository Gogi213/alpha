#!/usr/bin/env python3
"""G-022 independent-client A/B: conditions of every clock hour of the window, from raw files on disk.
Per hour: raw message rate of the independent clients (BingX rows/s per connection, Binance rows/s per kind), host from the 1 Hz vmstat
(us+sy, id, wa, st, seconds with steal >= 10 %), mean cores per command from pidstat (foreign load).
Usage: conditions.py --from <epoch s> --to <epoch s>   (run niced on srv)"""
import argparse, collections, datetime, glob, os, re, subprocess

import numpy as np
import pandas as pd

D = "/data/indep-ab/host"
IND_BINGX = os.environ.get("IND_BINGX", "/data/indep-ab/run1")
IND_BINANCE = os.environ.get("IND_BINANCE", "/data/indep-ab/run1bn")
OWN = ("pidstat", "vmstat", "iostat", "mpstat", "hostsample.sh", "perf", "sleep")  # sampler tools; clients and my analyses stay listed


def utc(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc)


def windows(t_from, t_to):
    out, a = [], t_from
    while a < t_to:
        b = min((int(a // 3600) + 1) * 3600, t_to)
        out.append((a, b))
        a = b
    return out


def cmd_name(c):
    t = c.split()
    if not t:
        return "?"
    b = os.path.basename(t[0])
    if b.startswith("python"):
        for x in t[1:]:
            if not x.startswith("-"):
                return os.path.basename(x)
    return b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="t_from", type=float, required=True)
    ap.add_argument("--to", dest="t_to", type=float, required=True)
    a = ap.parse_args()
    wins = windows(int(a.t_from), int(a.t_to))
    label = lambda w: f"{utc(w[0]).strftime('%H:%M:%S')}-{utc(w[1]).strftime('%H:%M:%S')}"

    print("## independent clients: raw rows/s (all frames, before dedup)")
    cnt = {}
    for f in sorted(glob.glob(f"{IND_BINGX}/*.tsv")):
        u = pd.read_csv(f, sep="\t", header=None, usecols=[1], names=["u"], dtype={"u": "int64"})["u"].to_numpy() / 1e9
        cnt[os.path.basename(f)[:-4]] = [int(((u >= w[0]) & (u < w[1])).sum()) for w in wins]
    print("window | " + " | ".join(sorted(cnt)) + " | mean rows/s per connection s03 | f00")
    for i, w in enumerate(wins):
        dt = w[1] - w[0]
        s03 = np.mean([cnt[c][i] for c in cnt if c.startswith("s03")]) / dt
        f00 = np.mean([cnt[c][i] for c in cnt if c.startswith("f00")]) / dt
        print(f"{label(w)} | " + " | ".join(f"{cnt[c][i] / dt:.1f}" for c in sorted(cnt)) + f" | {s03:.1f} | {f00:.1f}")
    print()
    bn = collections.defaultdict(lambda: [0] * len(wins))
    for f in sorted(glob.glob(f"{IND_BINANCE}/*.tsv")):
        d = pd.read_csv(f, sep="\t", header=None, usecols=[0, 2], names=["kind", "u"], dtype={"kind": "string", "u": "int64"})
        for kind, g in d.groupby("kind"):
            u = g["u"].to_numpy() / 1e9
            for i, w in enumerate(wins):
                bn[kind][i] += int(((u >= w[0]) & (u < w[1])).sum())
    print("Binance independent client rows/s: window | " + " | ".join(f"{k}" for k in sorted(bn)))
    for i, w in enumerate(wins):
        dt = w[1] - w[0]
        print(f"{label(w)} | " + " | ".join(f"{bn[k][i] / dt:.1f}" for k in sorted(bn)))
    print()

    print("## host (vmstat 1 Hz)")
    vm = [[] for _ in wins]
    for l in open(f"{D}/vmstat.log"):
        p = l.split()
        if len(p) < 19 or not re.match(r"\d{4}-\d\d-\d\d$", p[-2]):
            continue
        t = datetime.datetime.strptime(p[-2] + " " + p[-1], "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp()
        for i, w in enumerate(wins):
            if w[0] <= t < w[1]:
                vm[i].append((int(p[12]), int(p[13]), int(p[14]), int(p[15]), int(p[16]), int(p[11])))
    print("window | n s | us+sy % | idle % | wa % | steal % mean | steal max | seconds steal>=10 % | cs/s")
    for i, w in enumerate(wins):
        x = np.array(vm[i]) if vm[i] else np.zeros((0, 6))
        if len(x):
            print(f"{label(w)} | {len(x)} | {np.mean(x[:, 0] + x[:, 1]):.1f} | {np.mean(x[:, 2]):.1f} | {np.mean(x[:, 3]):.2f} | {np.mean(x[:, 4]):.2f} | {x[:, 4].max()} | {int((x[:, 4] >= 10).sum())} | {np.mean(x[:, 5]):.0f}")
    print()

    print("## mean cores per command (pidstat 1 Hz, sampler tools excluded), top 8 per window")
    acc = [collections.Counter() for _ in wins]
    nsec = [0] * len(wins)
    day = utc(wins[0][0]).strftime("%Y-%m-%d")
    base = datetime.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp()
    seen = [set() for _ in wins]
    for l in open(f"{D}/pidstat.log"):
        p = l.split(None, 13)
        if len(p) != 14 or not re.match(r"\d\d:\d\d:\d\d$", p[0]):
            continue
        h, m, s = map(int, p[0].split(":"))
        t = base + h * 3600 + m * 60 + s
        try:
            cpu = float(p[7])
        except ValueError:
            continue
        for i, w in enumerate(wins):
            if w[0] <= t < w[1]:
                seen[i].add(t)
                name = cmd_name(p[13])
                if name not in OWN:
                    acc[i][name] += cpu / 100
    for i, w in enumerate(wins):
        n = max(len(seen[i]), 1)
        top = acc[i].most_common(8)
        print(f"{label(w)} | " + "; ".join(f"{k} {v / n:.2f}" for k, v in top))


if __name__ == "__main__":
    main()
