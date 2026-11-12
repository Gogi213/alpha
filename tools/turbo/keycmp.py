#!/usr/bin/env python3
"""G-022 турбо: полнота против независимого приёмника — ключи записей (book: u, trade: a, mark: E) окна у Python-ядра и у Rust по одним и тем же потокам.
Запись, которая есть у одного и нет у другого ВНУТРИ окна (края `--edge-s` с каждой стороны отброшены: приём одной и той же записи у двух соединений
различается на сеть), — потеря у того, у кого её нет (или различие кадров, которое надо разобрать). Запуск (на srv):
  keycmp.py <каталог метки probe.sh> <корень Python, напр. /data/bnws> --out keycmp.json [--edge-s 5]
"""
import argparse
import glob
import io
import json
import os
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


def keys(root, lo_ms, hi_ms, since_mtime_s):
    """Ключи записей, у которых время БИРЖИ E в [lo, hi] (общее для обоих приёмников, в отличие от времени приёма); приём — с запасом ±20 с."""
    out = defaultdict(set)  # (тип, символ) -> ключи
    lo_ns, hi_ns = (lo_ms - 20_000) * 1_000_000, (hi_ms + 20_000) * 1_000_000
    for p in sorted(glob.glob(os.path.join(root, "20*", "*.tsv.zst"))):
        if os.path.getmtime(p) < since_mtime_s:
            continue
        for r in codec.decode(read_lines(p)):
            if r["t"] not in ("b", "t", "m") or not lo_ns <= r["rx_ns"] <= hi_ns or not lo_ms <= r["E"] <= hi_ms:
                continue
            k = r["u"] if r["t"] == "b" else r["a"] if r["t"] == "t" else r["E"]
            out[(r["t"], r["s"])].add(k)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("python_root")
    ap.add_argument("--out", required=True)
    ap.add_argument("--edge-s", type=float, default=5.0)
    a = ap.parse_args()
    t0, t1 = map(int, open(os.path.join(a.dir, "window-ms.txt")).read().split())
    lo, hi = int(t0 + a.edge_s * 1000), int(t1 - a.edge_s * 1000)
    py = keys(a.python_root, lo, hi, t0 / 1000 - 600)
    rs = keys(os.path.join(a.dir, "data"), lo, hi, 0)
    res = {"window_ms": [t0, t1], "edge_s": a.edge_s, "types": {}}
    for t in "btm":
        pn = rn = op = orr = 0
        ex = []
        for (tt, s) in set(py) | set(rs):
            if tt != t:
                continue
            a_, b_ = py.get((tt, s), set()), rs.get((tt, s), set())
            pn += len(a_)
            rn += len(b_)
            only_p, only_r = a_ - b_, b_ - a_
            op += len(only_p)
            orr += len(only_r)
            if (only_p or only_r) and len(ex) < 8:
                ex.append({"symbol": s, "only_python": sorted(only_p)[:3], "only_rust": sorted(only_r)[:3], "n_only_python": len(only_p), "n_only_rust": len(only_r)})
        res["types"][t] = {"python": pn, "rust": rn, "only_python": op, "only_rust": orr, "examples": ex}
    json.dump(res, open(a.out, "w"), indent=1)
    print(json.dumps({t: {k: v for k, v in x.items() if k != "examples"} for t, x in res["types"].items()}))


if __name__ == "__main__":
    main()
