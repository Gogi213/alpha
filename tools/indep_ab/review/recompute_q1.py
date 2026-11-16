#!/usr/bin/env python3
"""TK-535 review: independent recompute of Q1 (BingX) from raw files, without the executor's analyze.py.

python3 recompute_q1.py > q1.txt   (srv, niced)
"""
import itertools, subprocess, sys
import numpy as np
import pandas as pd

T0 = 1794846000  # 2026-11-16 16:20:00 UTC
T1 = 1794855930  # 19:05:30
RUN = "/data/indep-ab/run1"
PROD = {"f00": "/data/collect/bingx/data/2026-11-16/trade00-1794842766.tsv.zst",
        "s03": "/data/collect/bingx/data/2026-11-16/trade03-1794842766.tsv.zst"}
SYMS = {"f00": "/data/indep-ab/syms-trade00.txt", "s03": "/data/indep-ab/syms-trade03.txt"}
CONNS = {"f00": ["f000", "f001", "f002", "f003"], "s03": ["s030", "s031", "s032", "s033"]}


def q(a, ps=(5, 50, 95, 99)):
    a = np.asarray(a, float)
    return " ".join(f"p{p}={np.percentile(a, p):.2f}" for p in ps) if len(a) else "empty"


def load_ind(c):
    df = pd.read_csv(f"{RUN}/{c}.tsv", sep="\t", header=None, usecols=[0, 1, 2, 3, 4],
                     names=["k", "u", "sym", "id", "T"], dtype={"k": "Int64", "u": "int64", "sym": str, "id": "int64", "T": "int64"})
    df["k"] = df["k"].fillna(df["u"]).astype("int64")
    df = df[(df.k >= T0 * 10**9) & (df.k < T1 * 10**9)]
    dup = int(df.duplicated(["sym", "id"]).sum())
    df = df.sort_values("k").drop_duplicates(["sym", "id"])
    return df, dup


def load_prod(path, syms):
    p = subprocess.Popen(["zstd", "-dcq", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=1 << 22)
    rx, last, rows, nonmono = 0, {}, [], 0
    sb = {s.encode() for s in syms}
    for line in p.stdout:
        f = line.split(b"\t")
        try:
            d = int(f[1])
        except (ValueError, IndexError):
            continue
        if d < 0:
            nonmono += 1
        rx += d
        if f[0] != b"t":
            continue
        s = f[4]
        a = last.get(s, -1) + 1 + int(f[5])
        last[s] = a
        if s in sb and T0 * 10**9 <= rx < T1 * 10**9:
            rows.append((s.decode(), a, rx, rx // 1_000_000 - int(f[2]) - int(f[3])))
    p.wait()
    df = pd.DataFrame(rows, columns=["sym", "id", "rx", "T"])
    dup = int(df.duplicated(["sym", "id"]).sum())
    return df.sort_values("rx").drop_duplicates(["sym", "id"]), dup, nonmono


for st in ("s03", "f00"):
    syms = [l.strip() for l in open(SYMS[st]) if l.strip()]
    prod, pdup, nonmono = load_prod(PROD[st], syms)
    print(f"== {st}: prod rows {len(prod)} (dup rows dropped {pdup}, negative dns {nonmono})")
    print(f"PROD {st} lag(rx-T own) n={len(prod)} {q(prod.rx / 1e6 - prod['T'])}")
    ind = {}
    for c in CONNS[st]:
        df, dup = load_ind(c)
        ind[c] = df
        print(f"{c} n={len(df)} dupdrop={dup} lag {q(df.k / 1e6 - df['T'])}")
    # coverage, trimmed by T to avoid edge effects
    lo, hi = (T0 + 120) * 1000, (T1 - 120) * 1000
    pk = set(map(tuple, prod[(prod["T"] >= lo) & (prod["T"] < hi)][["sym", "id"]].values))
    ik = set()
    for df in ind.values():
        ik |= set(map(tuple, df[(df["T"] >= lo) & (df["T"] < hi)][["sym", "id"]].values))
    # ids outside the trimmed T range on the other side are not "missing": check membership in the full other set
    pall = set(map(tuple, prod[["sym", "id"]].values))
    iall = set()
    for df in ind.values():
        iall |= set(map(tuple, df[["sym", "id"]].values))
    print(f"coverage (T in window +-120s): prod {len(pk)} ind_union {len(ik)} only_ind {len(ik - pall)} only_prod {len(pk - iall)}")
    # wide table by id
    w = prod[["sym", "id", "rx", "T"]].rename(columns={"rx": "rx_prod", "T": "T_prod"})
    for c, df in ind.items():
        w = w.merge(df[["sym", "id", "k", "T"]].rename(columns={"k": "k_" + c, "T": "T_" + c}), on=["sym", "id"], how="outer")
    # prod T equal to which connection's T
    full = w.dropna()
    eq_any = np.zeros(len(full), bool)
    for c in CONNS[st]:
        e = (full["T_prod"] == full["T_" + c]).values
        eq_any |= e
        print(f"T_prod == T_{c}: {e.mean():.4f}")
    print(f"T_prod equals some ind T: {eq_any.mean():.4f} (n={len(full)})")
    kcols = ["k_" + c for c in CONNS[st]]
    d4 = (full[kcols].min(axis=1) - full["rx_prod"]) / 1e6
    print(f"first-of-4 - prod n={len(full)} {q(d4, (5, 25, 50, 75, 95))} share<0={(d4 < 0).mean():.4f}")
    for a, b in itertools.combinations(CONNS[st], 2):
        m = w[["k_" + a, "k_" + b, "T_" + a, "T_" + b, "rx_prod"]].dropna()
        darr = (m["k_" + a] - m["k_" + b]) / 1e6
        dT = m["T_" + a] - m["T_" + b]
        dmin = (m[["k_" + a, "k_" + b]].min(axis=1) - m["rx_prod"]) / 1e6
        corr = np.corrcoef(dT, darr)[0, 1] if dT.std() > 0 else float("nan")
        resid = darr - dT
        print(f"{a}+{b} n={len(m)} dT!=0={(dT != 0).mean():.4f} corr={corr:.3f} darr[{q(darr, (5, 50, 95))}] "
              f"min-prod[{q(dmin, (5, 50, 95))}] <-10={(dmin < -10).mean():.3f} |d|<=5={(dmin.abs() <= 5).mean():.3f} >10={(dmin > 10).mean():.3f} "
              f"resid[{q(resid, (5, 50, 95))}] dT[{q(dT, (5, 95))}]")
    # persistent offset of each connection vs median of 4 (same set), 10-min bins, arrival-based (not lag-based)
    full = full.copy()
    full["bin"] = (full["rx_prod"] // (600 * 10**9)).astype("int64")
    med = full[kcols].median(axis=1)
    for c in CONNS[st]:
        off = (full["k_" + c] - med) / 1e6
        g = off.groupby(full["bin"]).median()
        print(f"{c} arrival - median4 by 10min: min={g.min():.1f} max={g.max():.1f} mean={g.mean():.1f}")
        offT = (full["T_" + c] - full[["T_" + x for x in CONNS[st]]].median(axis=1))
        g2 = offT.groupby(full["bin"]).median()
        print(f"{c} T - medianT4 by 10min: min={g2.min():.1f} max={g2.max():.1f} mean={g2.mean():.1f}")
    sys.stdout.flush()
