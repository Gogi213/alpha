#!/usr/bin/env python3
"""G-022 turbo: what the two lines deliver and what de-duplication should do — from a full frame capture (ALPHA_COLLECT_CAPTURE=1).

Reads `capture-<shard>.tsv` (session, receive ns, frame: everything that arrived, before de-duplication) and, per (kind, symbol):
  identities    — exchange identity of each record (Binance: u / a / E; Bybit: book `seq`, trade UUID `i`, mark ts);
  coverage      — identities delivered by both lines / by one line only;
  skew          — |receive time difference| between the lines for identities seen on both (sizes the de-duplication window);
  old policy    — "key not greater than the last written" in arrival order (what Python and the first Rust version did): how many identities it lost;
  new policy    — exact identity within a window: duplicates dropped, everything else kept (loses nothing that arrived).
Usage: lanecmp.py <binance|bybit> <dir with capture-*.tsv> --out lanecmp.json [--window-s 60]
"""
import argparse
import glob
import json
import os
from collections import defaultdict

TRADE_SLOT = 1000


def records(ex, frame):
    """[(kind, symbol, key, identity)] of a frame; key = what the old policy ordered by (u/a/E), identity = what the new one dedups by."""
    try:
        d = json.loads(frame)
    except ValueError:
        return []
    if ex == "binance":
        x = d.get("data") if isinstance(d, dict) else None
        if isinstance(x, dict):
            if x.get("e") == "bookTicker":
                return [("b", x["s"], x["u"], x["u"])]
            if x.get("e") == "aggTrade":
                return [("t", x["s"], x["a"], x["a"])]
        elif isinstance(x, list):
            return [("m", y["s"], y["E"], y["E"]) for y in x if y.get("e") == "markPriceUpdate"]
        return []
    topic = d.get("topic", "") if isinstance(d, dict) else ""
    if topic.startswith("orderbook.1."):
        x = d["data"]
        return [("b", x["s"], x["u"], x["seq"])]  # old policy ordered by the connection-local u; the identity is the cross sequence
    if topic.startswith("publicTrade."):
        return [("t", x["s"], d["data"][i]["seq"] * TRADE_SLOT + i, x["i"]) for i, x in enumerate(d["data"])]
    if topic.startswith("tickers.") and d.get("type") == "delta" and any(k in d["data"] for k in ("markPrice", "indexPrice", "fundingRate", "nextFundingTime")):
        return [("m", d["data"]["symbol"], d["ts"], d["ts"])]
    return []


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ex")
    ap.add_argument("dir")
    ap.add_argument("--out", required=True)
    ap.add_argument("--skip-s", type=float, default=15.0, help="drop the first seconds (subscription snapshots)")
    ap.add_argument("--window-s", type=float, default=60.0)
    a = ap.parse_args()
    tot = defaultdict(lambda: defaultdict(int))
    skews = defaultdict(list)
    for p in sorted(glob.glob(os.path.join(a.dir, "capture-*.tsv"))):
        rows = []
        with open(p, encoding="utf-8", errors="replace") as f:
            for ln in f:
                parts = ln.rstrip("\n").split("\t", 2)
                if len(parts) == 3:
                    rows.append((int(parts[0]), int(parts[1]), parts[2]))
        if not rows:
            continue
        t_lo = rows[0][1] + int(a.skip_s * 1e9)
        order = defaultdict(list)  # (kind, sym) -> [(sender, rx, key, identity)] in arrival order
        for sender, rx, frame in rows:
            if rx < t_lo:
                continue
            for kind, sym, key, ident in records(a.ex, frame):
                order[(kind, sym)].append((sender, rx, key, ident))
        for (kind, sym), seq in order.items():
            g = tot[kind]
            senders = sorted({s for s, *_ in seq})
            g["symbols"] += 1
            g["deliveries"] += len(seq)
            # old policy: key greater than the last written key
            last, kept_old, kept_ids = None, 0, set()
            for _, _, key, ident in seq:
                if last is None or key > last:
                    last, kept_old = key, kept_old + 1
                    kept_ids.add(ident)
            # new policy: identity not seen within the window
            seen, kept_new, lost_new = {}, 0, 0
            by_id = defaultdict(dict)
            for s, rx, key, ident in seq:
                by_id[ident].setdefault(s, rx)
                t = seen.get(ident)
                if t is None or rx - t > a.window_s * 1e9:
                    seen[ident] = rx
                    kept_new += 1
            ids = set(by_id)
            g["identities"] += len(ids)
            g["written_old_policy"] += kept_old
            g["identities_lost_old_policy"] += len(ids - kept_ids)
            g["written_new_policy"] += kept_new
            g["identities_lost_new_policy"] += len(ids) - len({i for i in seen})
            g["duplicate_writes_new_policy"] += kept_new - len(ids)
            for ident, by in by_id.items():
                if len(by) >= 2:
                    g["on_both_lines"] += 1
                    v = sorted(by.values())
                    skews[kind].append((v[-1] - v[0]) / 1e6)
                else:
                    g["on_one_line_only"] += 1
            g["senders_max"] = max(g["senders_max"], len(senders))
    out = {"window_s": a.window_s, "kinds": {}}
    for kind, g in tot.items():
        sk = skews.get(kind, [])
        out["kinds"][kind] = dict(g) | {"skew_ms": {"n": len(sk), "p50": pct(sk, 0.5), "p99": pct(sk, 0.99), "p999": pct(sk, 0.999), "max": max(sk, default=None)}}
    json.dump(out, open(a.out, "w"), indent=1)
    print(json.dumps(out["kinds"]))


if __name__ == "__main__":
    main()
