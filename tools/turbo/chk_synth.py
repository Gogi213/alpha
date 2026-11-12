#!/usr/bin/env python3
"""G-022 турбо: итог ступени лестницы по файлам и статистике шарда — одинаково для Rust-коллектора и Python-ядра.
Запуск (на srv, python с zstandard): chk_synth.py <каталог ступени r<темп>> <темп> --out summary.json
Читает `data/20*/*.tsv.zst` (формат — docs/collector-ws-format.md), `stats-*.jsonl`, `health.json`, `winmon.json` ступени.
Считает: записей и записей/с на установившемся участке, пропуски и повторы u по символу (источник даёт u подряд), лаг «приём − E»
по записям (p50/p99/max), максимум паузы между записями; из stats — максимумы очередей и CPU шарда; из winmon — CPU и ожидание CPU юнита.
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


def read_lines(path):
    with open(path, "rb") as f:
        prev = None
        for ln in io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(f, read_across_frames=True), encoding="utf-8", errors="replace", newline="\n"):
            if prev is not None:
                yield prev
            prev = ln
        if prev is not None and prev.endswith("\n"):
            yield prev


def pct(cnt, p):
    n = sum(cnt.values())
    if not n:
        return None
    idx, seen = min(n - 1, int(n * p)), 0
    for v in sorted(cnt):
        seen += cnt[v]
        if seen > idx:
            return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("rate", type=int)
    ap.add_argument("--out", required=True)
    ap.add_argument("--skip-s", type=float, default=10.0, help="сколько секунд от первой записи не считать (подключение, подписка)")
    a = ap.parse_args()
    last, gaps, dups, lags, rows, first_ms, last_ms, busy = {}, 0, 0, Counter(), 0, None, None, set()
    nsym = set()
    for p in sorted(glob.glob(os.path.join(a.dir, "data", "20*", "*.tsv.zst"))):
        for r in codec.decode(read_lines(p)):
            if r["t"] != "b":
                continue
            rx_ms = r["rx_ns"] // 1_000_000
            if first_ms is None:
                first_ms = rx_ms
            if rx_ms < first_ms + a.skip_s * 1000:
                continue
            rows += 1
            last_ms = rx_ms
            nsym.add(r["s"])
            lags[rx_ms - r["E"]] += 1
            busy.add(rx_ms)
            u = last.get(r["s"])
            if u is not None:
                if r["u"] == u + 1:
                    pass
                elif r["u"] > u + 1:
                    gaps += r["u"] - u - 1
                else:
                    dups += 1
            last[r["s"]] = r["u"]
    span_s = (last_ms - first_ms - a.skip_s * 1000) / 1000 if rows and last_ms else None
    # максимальная пауза между соседними миллисекундами с записями
    pause = None
    if busy:
        s = sorted(busy)
        pause = max((b - a_ for a_, b in zip(s, s[1:])), default=None)
    st = []
    for p in glob.glob(os.path.join(a.dir, "data", "stats-*.jsonl")):
        st += [json.loads(ln) for ln in open(p)]
    st = [x for x in st if x.get("msgs") is not None]
    out = {
        "rate_target": a.rate, "rows": rows, "symbols": len(nsym), "span_s": round(span_s, 1) if span_s else None,
        "rows_per_s": round(rows / span_s) if span_s else None, "u_gaps": gaps, "u_dups_or_reorder": dups,
        "lag_ms": {"p50": pct(lags, 0.5), "p99": pct(lags, 0.99), "p999": pct(lags, 0.999), "max": max(lags, default=None)},
        "max_pause_ms": pause,
        "stats": {
            "windows": len(st), "msgs_per_s_mean": round(sum(x["msgs"] for x in st) / (10 * len(st))) if st else None,
            "cpu_frac_max": max((x.get("cpu_frac", 0) for x in st), default=None), "lag_p99_ms_max": max((x["lag_p99_ms"] for x in st if x.get("lag_p99_ms") is not None), default=None),
            "ring_depth_max": max((x.get("ring_depth_max", 0) for x in st), default=None), "ring_full": max((x.get("ring_full", 0) for x in st), default=None),
            "recvq_max": max((x.get("recvq_max", 0) for x in st), default=None), "busy_max_us": max((x.get("busy_max_us", 0) for x in st), default=None),
            "queue_lag_p99_ms_max": max((x["queue_lag_p99_ms"] for x in st if x.get("queue_lag_p99_ms") is not None), default=None),
            "max_gap_ms": max((x.get("max_gap_ms", 0) for x in st), default=None),
        },
    }
    for name in ("winmon.json", "health.json"):
        try:
            out[name[:-5]] = json.load(open(os.path.join(a.dir, name)))
        except (OSError, ValueError):
            pass
    json.dump(out, open(a.out, "w"), indent=1)
    print(json.dumps({k: out[k] for k in ("rate_target", "rows_per_s", "u_gaps", "u_dups_or_reorder", "lag_ms", "max_pause_ms")}))


if __name__ == "__main__":
    main()
