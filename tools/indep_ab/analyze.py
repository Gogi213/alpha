#!/usr/bin/env python3
"""G-022 independent-client A/B: compare iclient.py / iclient_bn.py logs with the collector files (tsv.zst) per trade id.

Run on srv (numpy + pandas), niced:  python3 analyze.py bingx|binance --from <epoch s> --to <epoch s> [--json out.json]

BingX:  per-connection lag (kernel arrival - exchange T), per-trade arrival difference independent - prod (same id),
        coverage both ways, spread between independent connections, user-space delay (u - k) per connection,
        profile around every full hour.
Binance: same for aggTrade (a) and bookTicker (u) against prod `t`/`b` rows.
"""
import argparse, collections, datetime, glob, json, os, subprocess, sys

import numpy as np
import pandas as pd

BINGX_PROD = "/data/collect/bingx/data"
BINANCE_PROD = "/data/collect/binance/data"
IND_BINGX = "/data/indep-ab/run1"
IND_BINANCE = "/data/indep-ab/run1bn"
SETS = {"s03": "/data/indep-ab/syms-trade03.txt", "f00": "/data/indep-ab/syms-trade00.txt"}
PCT = [1, 5, 25, 50, 75, 95, 99, 99.9]


def pct(a, qs=PCT):
    a = np.asarray(a, dtype=float)
    if len(a) == 0:
        return {f"p{q}": None for q in qs}
    v = np.percentile(a, qs)
    return {f"p{q}": round(float(x), 2) for q, x in zip(qs, v)}


# ---- prod decode (format: docs/collector-ws-format.md; delta state per symbol, dns = rx delta of every line) ----------
def decode_prod(path, want_syms, kinds, t_from_ns, t_to_ns):
    """Yield (kind, rx_ns, sym, key, lag_ms, et_ms) for rows of `kinds` ('t','b') whose sym in want_syms and rx in window."""
    p = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE, bufsize=1 << 22)
    rx = 0
    last = {}
    out = []
    kinds_b = {k.encode() for k in kinds}
    syms_b = {s.encode() for s in want_syms}
    for line in p.stdout:
        f = line.rstrip(b"\n").split(b"\t")
        try:
            rx += int(f[1])
        except (ValueError, IndexError):
            continue
        k = f[0]
        if k not in kinds_b or len(f) < 6:
            continue
        sym = f[4]
        if sym not in syms_b:
            continue
        d = int(f[5])
        if k == b"t":
            a = last.get((k, sym), 0) + d
            last[(k, sym)] = a + 1
        else:
            a = last.get((k, sym), 0) + d
            last[(k, sym)] = a
        if t_from_ns <= rx <= t_to_ns:
            out.append((k.decode(), rx, sym.decode(), a, int(f[2]), int(f[3])))
    p.wait()
    return out


def prod_files(root, prefixes, t_from, t_to, day_list, want_syms=None):
    """Files of runs that overlap [t_from, t_to] (run id in the name = start epoch s; mtime = last write); with want_syms
    only files whose first ~8 MB of text mention one of the symbols (each shard carries a fixed symbol set)."""
    import re
    files = []
    for day in day_list:
        for pre in prefixes:
            for f in sorted(glob.glob(f"{root}/{day}/{pre}*.tsv.zst")):
                m = re.search(r"-(\d+)\.tsv\.zst$", f)
                if not m or int(m.group(1)) > t_to or os.path.getmtime(f) < t_from - 60:
                    continue
                if want_syms is not None:
                    head = subprocess.run(f"zstd -dc '{f}' 2>/dev/null | head -c 8000000", shell=True, capture_output=True).stdout
                    if not any(b"	" + s.encode() + b"	" in head for s in want_syms):
                        continue
                files.append(f)
    return files


def days_between(t_from, t_to):
    d0 = datetime.datetime.fromtimestamp(t_from, datetime.timezone.utc).date()
    d1 = datetime.datetime.fromtimestamp(t_to, datetime.timezone.utc).date()
    out, d = [], d0
    while d <= d1:
        out.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return out


def hour_marks(t_from, t_to):
    h = int(t_from // 3600 + 1) * 3600
    while h <= t_to:
        yield h
        h += 3600


def profile(df, col_lag, col_uk, t_col, marks, edges, label):
    """df: rows with epoch-second column `t_col` (float), lag ms, optional u-k ms; per window relative to each hour."""
    out = {}
    for h in marks:
        rows = []
        for a, b in zip(edges[:-1], edges[1:]):
            m = (df[t_col] >= h + a) & (df[t_col] < h + b)
            x = df[m]
            row = {"win": f"{a:+d}..{b:+d}", "n": int(len(x))}
            if len(x):
                row["lag_p50"] = round(float(np.percentile(x[col_lag], 50)), 1)
                row["lag_p99"] = round(float(np.percentile(x[col_lag], 99)), 1)
                row["lag_max"] = round(float(x[col_lag].max()), 1)
                if col_uk and col_uk in x:
                    row["uk_p99"] = round(float(np.percentile(x[col_uk], 99)), 3)
                    row["uk_max"] = round(float(x[col_uk].max()), 3)
            rows.append(row)
        out[datetime.datetime.fromtimestamp(h, datetime.timezone.utc).strftime("%H:%M")] = rows
    return out



def arrival_gaps(k_ns_sorted, marks, top=8):
    """Largest gap between consecutive kernel arrival stamps (all connections pooled), per hour mark (-30..+60 s) and overall.
    A whole-VM stall shows as a gap with zero arrivals followed by a burst with stamps just after the gap."""
    k = np.asarray(k_ns_sorted, dtype="int64")
    d = np.diff(k) / 1e6
    out = {"overall_top": [], "by_hour": {}}
    idx = np.argsort(d)[::-1][:top]
    for i in sorted(idx):
        out["overall_top"].append({"start": datetime.datetime.fromtimestamp(k[i] / 1e9, datetime.timezone.utc).strftime("%H:%M:%S.%f")[:-3], "gap_ms": round(float(d[i]), 1)})
    for h in marks:
        m = (k[:-1] >= (h - 30) * 10**9) & (k[:-1] < (h + 60) * 10**9)
        if m.any():
            j = np.argmax(np.where(m, d, -1))
            t0 = k[j] / 1e9
            burst = int(((k >= k[j + 1]) & (k < k[j + 1] + 50_000_000)).sum())
            out["by_hour"][datetime.datetime.fromtimestamp(h, datetime.timezone.utc).strftime("%H:%M")] = {
                "max_gap_ms": round(float(d[j]), 1),
                "gap_start_rel_hour_s": round(t0 - h, 3),
                "rows_in_first_50ms_after": burst,
                "median_gap_ms_in_window": round(float(np.median(d[m])), 2),
            }
    return out

EDGES = [-60, -30, -10, 0, 5, 10, 20, 30, 60, 120]


# ---- BingX ------------------------------------------------------------------------------------------------------
def load_ind_bingx(t_from, t_to):
    frames = []
    for f in sorted(glob.glob(f"{IND_BINGX}/*.tsv")):
        name = os.path.basename(f)[:-4]
        df = pd.read_csv(
            f, sep="\t", header=None, names=["k", "u", "sym", "id", "T", "E"],
            dtype={"k": "float64", "u": "int64", "sym": "string", "id": "int64", "T": "int64"}, usecols=[0, 1, 2, 3, 4],
        )
        df["conn"] = name
        df["set"] = name[:3]
        df["ip"] = name[3:]
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    df = df[(df["u"] >= t_from * 10**9) & (df["u"] <= t_to * 10**9)]
    df["k"] = df["k"].fillna(df["u"]).astype("int64")
    df = df.sort_values("k").drop_duplicates(["conn", "sym", "id"], keep="first")
    df["lag"] = df["k"] / 1e6 - df["T"]
    df["uk"] = (df["u"] - df["k"]) / 1e6
    return df


def bingx(a):
    t_from, t_to = a.t_from, a.t_to
    ind = load_ind_bingx(t_from, t_to)
    res = {"window_utc": [datetime.datetime.fromtimestamp(x, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S") for x in (t_from, t_to)]}
    # prod rows for the two symbol sets
    symsets = {n: [l.strip() for l in open(p) if l.strip()] for n, p in SETS.items()}
    allsyms = set(sum(symsets.values(), []))
    files = prod_files(BINGX_PROD, ["trade0"], t_from, t_to, days_between(t_from, t_to), allsyms)
    rows = []
    for f in files:
        rows += decode_prod(f, allsyms, "t", t_from * 10**9, t_to * 10**9)
    prod = pd.DataFrame(rows, columns=["kind", "rx", "sym", "id", "lag_e", "et"])
    prod = prod.sort_values("rx").drop_duplicates(["sym", "id"], keep="first")
    prod["set"] = np.where(prod["sym"].isin(symsets["s03"]), "s03", "f00")
    prod["lag"] = prod["rx"] / 1e6 - ((prod["rx"] // 1_000_000) - prod["lag_e"] - prod["et"])  # rx_ms - T, T = e - ET
    res["prod_files"] = files
    res["prod_rows"] = int(len(prod))
    res["ind_rows"] = int(len(ind))
    # ---- per-connection lag
    lagtab = {}
    for c, g in ind.groupby("conn"):
        lagtab[c] = {"n": int(len(g)), **pct(g["lag"]), "uk": pct(g["uk"], [50, 99, 99.9, 100])}
    for s, g in prod.groupby("set"):
        lagtab["PROD-" + s] = {"n": int(len(g)), **pct(g["lag"])}
    res["lag_by_conn"] = lagtab
    # ---- per-trade difference ind - prod
    diff = {}
    for c, g in ind.groupby("conn"):
        m = g.merge(prod[["sym", "id", "rx"]], on=["sym", "id"], how="inner")
        d = (m["k"] - m["rx"]) / 1e6
        diff[c] = {"matched": int(len(m)), "of_ind": int(len(g)), "neg_share": round(float((d < 0).mean()), 4), "mean": round(float(d.mean()), 2), **pct(d, [5, 25, 50, 75, 95, 99])}
    res["diff_ind_minus_prod_ms"] = diff
    # ---- coverage both ways (strip 120 s at both edges) and per-set best-of-N
    lo, hi = (t_from + 120) * 10**9, (t_to - 120) * 10**9
    cov = {}
    for s in ("s03", "f00"):
        gi = ind[(ind["set"] == s) & (ind["k"] >= lo) & (ind["k"] <= hi)]
        pi = prod[(prod["set"] == s) & (prod["rx"] >= lo) & (prod["rx"] <= hi)]
        key_i = set(zip(gi["sym"], gi["id"]))
        key_p = set(zip(pi["sym"], pi["id"]))
        cov[s] = {"ind_union": len(key_i), "prod": len(key_p), "both": len(key_i & key_p), "only_ind": len(key_i - key_p), "only_prod": len(key_p - key_i)}
        # first arrival over the 4 independent connections vs prod
        first = gi.groupby(["sym", "id"])["k"].min().rename("kmin").reset_index()
        m = first.merge(pi[["sym", "id", "rx"]], on=["sym", "id"])
        d = (m["kmin"] - m["rx"]) / 1e6
        cov[s]["first_of_4_minus_prod_ms"] = {"n": int(len(m)), "neg_share": round(float((d < 0).mean()), 4), **pct(d, [5, 25, 50, 75, 95, 99])}
        # pairs of connections: min over the pair vs prod (prod has 2 lanes)
        pairs = {}
        names = sorted(gi["conn"].unique())
        wide = gi.pivot_table(index=["sym", "id"], columns="conn", values="k", aggfunc="min")
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                x = wide[[names[i], names[j]]].dropna()
                pr = pi.set_index(["sym", "id"])["rx"]
                mm = x.join(pr, how="inner")
                dmin = (mm[[names[i], names[j]]].min(axis=1) - mm["rx"]) / 1e6
                dij = (mm[names[i]] - mm[names[j]]) / 1e6
                pairs[f"{names[i]}|{names[j]}"] = {"n": int(len(mm)), "min_pair_minus_prod_p50": round(float(dmin.median()), 2), "i_minus_j_p50": round(float(dij.median()), 2), "i_minus_j_p5": round(float(dij.quantile(0.05)), 2), "i_minus_j_p95": round(float(dij.quantile(0.95)), 2)}
        cov[s]["pairs"] = pairs
    res["coverage"] = cov
    # ---- lag over time, 10 min medians
    ind["bucket"] = (ind["k"] // (600 * 10**9)) * 600
    prod["bucket"] = (prod["rx"] // (600 * 10**9)) * 600
    ts = ind.groupby(["conn", "bucket"])["lag"].median().unstack(0).round(1)
    tp = prod.groupby(["set", "bucket"])["lag"].median().unstack(0).round(1)
    tt = ts.join(tp.add_prefix("PROD-"), how="outer")
    tt.index = [datetime.datetime.fromtimestamp(i, datetime.timezone.utc).strftime("%H:%M") for i in tt.index]
    res["lag_p50_by_10min"] = json.loads(tt.to_json(orient="index"))
    # ---- ping RTT per connection (client side)
    rtts = {}
    for f in sorted(glob.glob(f"{IND_BINGX}/*.ev.jsonl")):
        v = [json.loads(l) for l in open(f)]
        r = [e["rtt_us"] / 1000 for e in v if e["ev"] == "pong" and e["ts_ns"] >= t_from * 10**9]
        peers = {e["peer"] for e in v if e["ev"] == "connected"}
        closes = sum(1 for e in v if e["ev"] in ("closed", "give_up"))
        rtts[os.path.basename(f)[:-9]] = {"peer": sorted(peers), "pings": len(r), **pct(r, [5, 50, 95, 99]), "closes": closes}
    res["ws_ping_rtt_ms"] = rtts
    # ---- profile around hour marks
    marks = list(hour_marks(t_from, t_to))
    ind["t"] = ind["k"] / 1e9
    prod["t"] = prod["rx"] / 1e9
    res["profile_ind"] = profile(ind, "lag", "uk", "t", marks, EDGES, "ind")
    res["profile_prod"] = profile(prod, "lag", None, "t", marks, EDGES, "prod")
    # baseline outside :00 minutes
    base = ind[(ind["t"] % 3600) > 120]
    res["ind_baseline_outside_00"] = {"lag": pct(base["lag"]), "uk": pct(base["uk"], [50, 99, 99.9, 100])}
    base_p = prod[(prod["t"] % 3600) > 120]
    res["prod_baseline_outside_00"] = {"lag": pct(base_p["lag"])}
    # per-second extremes of u-k anywhere
    ind["sec"] = ind["t"].astype("int64")
    ukmax = ind.groupby("sec")["uk"].max()
    top = ukmax.sort_values(ascending=False).head(10)
    res["uk_max_seconds"] = {datetime.datetime.fromtimestamp(int(s), datetime.timezone.utc).strftime("%H:%M:%S"): round(float(v), 2) for s, v in top.items()}
    res["arrival_gaps_ind_pooled"] = arrival_gaps(np.sort(ind["k"].to_numpy()), marks)
    res["arrival_gaps_prod_pooled"] = arrival_gaps(np.sort(prod["rx"].to_numpy()), marks)
    return res


# ---- Binance ----------------------------------------------------------------------------------------------------
def binance(a):
    t_from, t_to = a.t_from, a.t_to
    syms = set()
    ind_frames = []
    for f in sorted(glob.glob(f"{IND_BINANCE}/*.tsv")):
        df = pd.read_csv(f, sep="\t", header=None, names=["kind", "k", "u", "sym", "id", "T", "E"], dtype={"k": "float64", "u": "int64", "sym": "string", "id": "int64", "T": "int64", "E": "int64"})
        ind_frames.append(df)
    ind = pd.concat(ind_frames, ignore_index=True)
    ind = ind[(ind["u"] >= t_from * 10**9) & (ind["u"] <= t_to * 10**9)]
    ind["k"] = ind["k"].fillna(ind["u"]).astype("int64")
    ind = ind.drop_duplicates(["kind", "sym", "id"], keep="first")
    ind["lag"] = ind["k"] / 1e6 - ind["E"]
    ind["uk"] = (ind["u"] - ind["k"]) / 1e6
    ind["t"] = ind["k"] / 1e9
    syms = set(ind["sym"].unique())
    files = prod_files(BINANCE_PROD, ["book", "trade"], t_from, t_to, days_between(t_from, t_to), syms)
    rows = []
    for f in files:
        kind = "b" if os.path.basename(f).startswith("book") else "t"
        rows += decode_prod(f, syms, kind, t_from * 10**9, t_to * 10**9)
    prod = pd.DataFrame(rows, columns=["kind", "rx", "sym", "id", "lag_e", "et"])
    prod = prod.sort_values("rx").drop_duplicates(["kind", "sym", "id"], keep="first")
    prod["lag"] = prod["lag_e"].astype(float)
    prod["t"] = prod["rx"] / 1e9
    res = {"window_utc": [datetime.datetime.fromtimestamp(x, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S") for x in (t_from, t_to)], "prod_files": files, "ind_rows": int(len(ind)), "prod_rows": int(len(prod)), "symbols": sorted(syms)}
    for kind in ("b", "t"):
        gi, gp = ind[ind["kind"] == kind], prod[prod["kind"] == kind]
        m = gi.merge(gp[["sym", "id", "rx"]], on=["sym", "id"])
        d = (m["k"] - m["rx"]) / 1e6
        res[f"diff_ind_minus_prod_ms_{kind}"] = {"matched": int(len(m)), "of_ind": int(len(gi)), "neg_share": round(float((d < 0).mean()), 4), **pct(d, [1, 5, 25, 50, 75, 95, 99, 99.9])}
        res[f"lag_{kind}"] = {"ind": pct(gi["lag"]), "prod": pct(gp["lag"]), "ind_uk": pct(gi["uk"], [50, 99, 99.9, 100])}
        marks = list(hour_marks(t_from, t_to))
        res[f"profile_ind_{kind}"] = profile(gi, "lag", "uk", "t", marks, EDGES, "ind")
        res[f"profile_prod_{kind}"] = profile(gp, "lag", None, "t", marks, EDGES, "prod")
        # per-window rate (frames/s) of independent client around :00
    gi = ind
    gi["sec"] = gi["t"].astype("int64")
    rate = gi.groupby("sec").size()
    prof = {}
    for h in hour_marks(t_from, t_to):
        r = rate.reindex(range(h - 30, h + 60), fill_value=0)
        prof[datetime.datetime.fromtimestamp(h, datetime.timezone.utc).strftime("%H:%M")] = {"msgs_per_s_before(-30..-1)": round(float(r.iloc[:30].mean()), 1), "msgs_per_s_0..10": round(float(r.iloc[30:40].mean()), 1), "msgs_per_s_10..60": round(float(r.iloc[40:].mean()), 1)}
    res["ind_rate_around_hour"] = prof
    ukmax = ind.groupby("sec")["uk"].max().sort_values(ascending=False).head(10)
    res["uk_max_seconds"] = {datetime.datetime.fromtimestamp(int(s), datetime.timezone.utc).strftime("%H:%M:%S"): round(float(v), 2) for s, v in ukmax.items()}
    res["arrival_gaps_ind_pooled"] = arrival_gaps(np.sort(ind["k"].to_numpy()), list(hour_marks(t_from, t_to)))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["bingx", "binance"])
    ap.add_argument("--from", dest="t_from", type=float, required=True)
    ap.add_argument("--to", dest="t_to", type=float, required=True)
    ap.add_argument("--json")
    a = ap.parse_args()
    res = bingx(a) if a.what == "bingx" else binance(a)
    s = json.dumps(res, indent=1, ensure_ascii=False, default=str)
    if a.json:
        open(a.json, "w").write(s)
    print(s)


if __name__ == "__main__":
    main()
