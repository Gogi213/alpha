#!/usr/bin/env python3
"""G-022 Gate: side-by-side check of the Rust collector against an independent raw capture of the same exchange at the same time (the oracle for the book and the marks, which REST cannot give).

  gate_shadow_cmp.py <collector data root> <frames prefix> <t0_ms> [--from-s 20 --to-s 170]
The raw capture is `docs/findings/gate-ws-prep-2026-11-15/w.py` (one Python connection per channel, one subscription per contract, every frame kept with its receive time): <frames prefix>-book.jsonl,
-trade.jsonl, -mark.jsonl. Keys: book (symbol, u), trade (contract, id), mark (contract, t); the window is [t0 + from, t0 + to) by exchange time (book `t`, trade `create_time_ms`, ticker `t`).
Per channel: keys of the raw capture, keys of the collector's files, common, only in the raw capture (= a record the exchange delivered to the independent client and the collector did not write: the loss signal),
only in the collector (the collector has two lines per shard, the raw capture one connection: the second line brings states the single connection did not get — book and mark are sampled per connection).
"""
import argparse
import glob
import io
import json
import os
import sys
from collections import Counter

import zstandard

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collector"))
from wscore import codec  # noqa: E402


def raw_keys(path, lo, hi):
    out = {"book": set(), "trade": set(), "mark": set()}
    for ln in open(path, encoding="utf-8"):
        j = json.loads(ln)
        if "f" not in j:
            continue
        d = json.loads(j["f"])
        if d.get("event") != "update":
            continue
        r = d["result"]
        if d["channel"] == "futures.book_ticker":
            if lo <= r["t"] < hi:
                out["book"].add((r["s"], r["u"]))
        elif d["channel"] == "futures.trades":
            for x in r:
                if lo <= x["create_time_ms"] < hi:
                    out["trade"].add((x["contract"], x["id"]))
        elif d["channel"] == "futures.tickers":
            for x in r:
                if lo <= x["t"] < hi:
                    out["mark"].add((x["contract"], x["t"]))
    return out


def file_keys(root, lo, hi):
    out = {"book": set(), "trade": set(), "mark": set()}
    for p in sorted(glob.glob(os.path.join(root, "20*", "*.tsv.zst"))):
        with open(p, "rb") as f:
            lines = [ln for ln in io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(f, read_across_frames=True), encoding="utf-8", newline="\n") if ln.strip()]
        for r in codec.decode(iter(lines)):
            if r["t"] == "b" and lo <= r["E"] < hi:
                out["book"].add((r["s"], r["u"]))
            elif r["t"] == "t" and lo <= r["T"] < hi:
                out["trade"].add((r["s"], r["a"]))
            elif r["t"] == "m" and lo <= r["E"] < hi:
                out["mark"].add((r["s"], r["E"]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("prefix")
    ap.add_argument("t0", type=int)
    ap.add_argument("--from-s", type=float, default=20)
    ap.add_argument("--to-s", type=float, default=170)
    a = ap.parse_args()
    lo, hi = a.t0 + int(a.from_s * 1000), a.t0 + int(a.to_s * 1000)
    raw = {}
    for kind in ("book", "trade", "mark"):
        raw[kind] = raw_keys(f"{a.prefix}-{kind}.jsonl", lo, hi)[kind]
    rs = file_keys(a.root, lo, hi)
    res = {}
    for kind in ("book", "trade", "mark"):
        r, f = raw[kind], rs[kind]
        only_raw = r - f
        by_sym = Counter(k[0] for k in only_raw)
        res[kind] = {"raw": len(r), "collector": len(f), "common": len(r & f), "only_raw": len(only_raw), "only_collector": len(f - r), "only_raw_share": round(len(only_raw) / len(r), 6) if r else None,
                     "only_raw_symbols": len(by_sym), "only_raw_top": by_sym.most_common(5), "only_raw_examples": sorted(only_raw)[:5]}
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
