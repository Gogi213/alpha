#!/usr/bin/env python3
"""G-022 turbo: acceptance of recorded files for the identity-dedup format (arrival order; keys may be out of order, duplicates may not).

Duplicates, order and gaps are judged per run (process start): a restart leaves a gap of a few seconds and may repeat the last state.
Per file: closed files must pass `zstd -t` and match their `.ok` row count; every stream record kind is checked per symbol:
  duplicates   — the same key twice in one symbol (b: u [Bybit: seq], t: a, m: E) = the de-duplication failed (must be 0);
  out of order — key lower than the previous record of the symbol (informational: expected between two lines; readers sort by key);
  gaps         — Binance trades only: after sorting, ids must be consecutive (`a` has no holes); other kinds have no completeness rule in the stream itself
                 (compare with the exchange: tools/turbo/restcheck.py);
  rx order     — receive time never decreases inside a file (`dns` >= 0), `closed` is the last control row of a closed file.
Usage: check_files.py <root> [--exchange binance|bybit] [--since-ms T0] [--until-ms T1] --out check.json
"""
import argparse
import glob
import io
import json
import os
import subprocess
import sys
from collections import defaultdict

import zstandard

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collector"))
from wscore import codec  # noqa: E402


def read_lines(path):
    with open(path, "rb") as f:
        prev = None
        for ln in io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(f, read_across_frames=True), encoding="utf-8", errors="replace", newline="\n"):
            if prev is not None:
                yield prev
            prev = ln
        if prev is not None and prev.endswith("\n"):
            yield prev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--exchange", default="binance")
    ap.add_argument("--since-ms", type=int, default=0)
    ap.add_argument("--until-ms", type=int, default=1 << 62)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    files, tot = [], defaultdict(int)
    keys = defaultdict(list)  # (run, kind, symbol) -> keys in file order; a process restart is a boundary (a few seconds are not recorded, the last state may repeat)
    for p in sorted(glob.glob(os.path.join(a.root, "20*", "*.tsv.zst"))):
        ok_path = p + ".ok"
        closed = os.path.exists(ok_path)
        info = {"file": os.path.relpath(p, a.root), "closed": closed}
        run = os.path.basename(p).rsplit("-", 1)[-1].split(".")[0]
        rows, prev_rx, rx_back, last_ctl = 0, 0, 0, None
        for r in codec.decode(read_lines(p)):
            rows += 1
            if r["rx_ns"] < prev_rx:
                rx_back += 1
            prev_rx = r["rx_ns"]
            if r["t"] == "c":
                last_ctl = r["what"]
            if r["t"] in ("b", "t", "m") and a.since_ms * 1_000_000 <= r["rx_ns"] <= a.until_ms * 1_000_000:
                k = r["u"] if r["t"] == "b" else r["a"] if r["t"] == "t" else r["E"]
                keys[(run, r["t"], r["s"])].append(k)
        info["rows"], info["rx_decreases"] = rows, rx_back
        if closed:
            info["ok_rows"] = int(open(ok_path).read().strip())
            info["rows_match_ok"] = info["ok_rows"] == rows
            info["zstd_t"] = subprocess.run(["zstd", "-tq", p], capture_output=True).returncode == 0
            info["last_control"] = last_ctl
        files.append(info)
    per = defaultdict(lambda: defaultdict(int))
    for (run, kind, sym), ks in keys.items():
        g = per[kind]
        g["symbol_runs"] += 1
        g["records"] += len(ks)
        g["duplicates"] += len(ks) - len(set(ks))
        g["out_of_order"] += sum(1 for x, y in zip(ks, ks[1:]) if y < x)
        if kind == "t" and a.exchange == "binance":
            s = sorted(set(ks))
            missing = sum(y - x - 1 for x, y in zip(s, s[1:]) if y > x + 1)
            g["id_gaps"] += sum(1 for x, y in zip(s, s[1:]) if y > x + 1)
            g["id_gap_ids"] += missing
    bad = [f for f in files if f["closed"] and (not f.get("zstd_t") or not f.get("rows_match_ok") or f.get("last_control") != "closed")]
    out = {"files": len(files), "closed_files": sum(f["closed"] for f in files), "closed_files_bad": len(bad), "bad_examples": bad[:5],
           "rx_decreases": sum(f["rx_decreases"] for f in files), "kinds": {k: dict(v) for k, v in per.items()}}
    json.dump(out | {"per_file": files}, open(a.out, "w"), indent=1)
    print(json.dumps({k: out[k] for k in ("files", "closed_files", "closed_files_bad", "rx_decreases", "kinds")}))
    return 0 if not bad and all(v.get("duplicates", 0) == 0 for v in per.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
