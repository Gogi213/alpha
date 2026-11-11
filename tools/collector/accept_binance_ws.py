#!/usr/bin/env python3
"""TK-527: приёмка по записи за окно (В-139: 10 минут): разрывы/таймауты/переподключения, лаги, полнота по u/a, размер, health.json.
Запуск: accept_binance_ws.py --root /data/bnws --since-ms <старт окна> --until-ms <конец> --out /data/bnws/accept.json
"""
import argparse
import glob
import json
import os
import sys

import zstandard

sys.path.insert(0, os.path.dirname(__file__))
import binance_ws as bw  # noqa: E402


def read_file(path):
    """Закрытый файл (есть .ok) — целиком; открытый — без последней строки (запись могла оборваться на середине)."""
    with open(path, "rb") as f:
        text = zstandard.ZstdDecompressor().stream_reader(f, read_across_frames=True).read().decode(errors="replace")
    return text if text.endswith("\n") else text[: text.rfind("\n") + 1]  # открытый файл (или обрыв записи) — без неполной последней строки


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--since-ms", type=int, required=True)
    ap.add_argument("--until-ms", type=int, required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    ev = {}
    for p in glob.glob(os.path.join(a.root, "events-*.jsonl")):
        for ln in open(p):
            e = json.loads(ln)
            if a.since_ms <= e["ts_ms"] <= a.until_ms:
                ev.setdefault(e["ev"], []).append({k: e[k] for k in ("shard", "ts_ms") } | {k: v for k, v in e.items() if k in ("what", "err")})
    st = {}
    for p in glob.glob(os.path.join(a.root, "stats-*.jsonl")):
        shard = os.path.basename(p)[6:-6]
        rows = [json.loads(ln) for ln in open(p)]
        rows = [r for r in rows if a.since_ms <= r["ts_ms"] <= a.until_ms]
        lag50 = [r["lag_p50_ms"] for r in rows if r["lag_p50_ms"] is not None]
        st[shard] = {"windows": len(rows), "msgs": sum(r["msgs"] for r in rows), "max_gap_ms": max((r["max_gap_ms"] for r in rows), default=None),
                     "lag_p50_ms_median": bw.percentile(lag50, 0.5), "lag_p50_ms_max": max(lag50, default=None),
                     "lag_p99_ms_max": max((r["lag_p99_ms"] for r in rows if r["lag_p99_ms"] is not None), default=None),
                     "cpu_frac_max": max((r["cpu_frac"] for r in rows), default=None)}
    rec, gaps, size = {"b": 0, "t": 0, "m": 0, "g": 0, "c": 0, "r": 0}, {"book_u_not_increasing": 0, "trade_a_gaps": 0, "trade_a_gap_ids": 0, "trade_dups_across_sessions": 0, "trade_raw_nq": 0}, 0
    pings, raw_samples, ids = [], [], {}
    for p in sorted(glob.glob(os.path.join(a.root, "20*", "*.tsv.zst"))):
        size += os.path.getsize(p)
        lastu = {}  # u строго растёт внутри файла (одна сессия); файлы соседних сессий на перекрытии пишут одни и те же кадры
        for r in bw.decode(read_file(p)):
            if not (a.since_ms * 1_000_000 <= r["rx_ns"] <= a.until_ms * 1_000_000):
                continue
            rec[r["t"]] += 1
            if r["t"] == "g":
                pings.append(r["rtt_us"] / 1000)
            elif r["t"] == "r":
                if len(raw_samples) < 5:
                    raw_samples.append(r["frame"][:200])
                if '@aggTrade"' in r["frame"][:60]:  # aggTrade с nq != q (RPI) хранится сырым кадром — тоже сделка потока a
                    try:
                        d = json.loads(r["frame"])["data"]
                        seen = ids.setdefault(d["s"], set())
                        if d["a"] in seen:
                            gaps["trade_dups_across_sessions"] += 1
                        seen.add(d["a"])
                        gaps["trade_raw_nq"] += 1
                    except (ValueError, KeyError, TypeError):
                        pass
            elif r["t"] == "b":
                if r["s"] in lastu and r["u"] <= lastu[r["s"]]:
                    gaps["book_u_not_increasing"] += 1
                lastu[r["s"]] = r["u"]
            elif r["t"] == "t":
                seen = ids.setdefault(r["s"], set())
                if r["a"] in seen:
                    gaps["trade_dups_across_sessions"] += 1
                seen.add(r["a"])
    for seen in ids.values():  # полнота aggTrade по a (подряд идущие id на символ) — по объединению всех файлов сессий
        xs = sorted(seen)
        for x, y in zip(xs, xs[1:]):
            if y != x + 1:
                gaps["trade_a_gaps"] += 1
                gaps["trade_a_gap_ids"] += y - x - 1
    health = json.load(open(os.environ.get("HEALTH", "/data/health/binance.json")))
    out = {"window_ms": [a.since_ms, a.until_ms], "events": {k: len(v) for k, v in ev.items()}, "events_detail": {k: v[:20] for k, v in ev.items() if k != "connected" and k != "subscribed"},
           "shards": st, "records": rec, "sequence_checks": gaps, "ping_ms": {"n": len(pings), "p50": bw.percentile(pings, 0.5), "p99": bw.percentile(pings, 0.99), "max": max(pings, default=None)},
           "raw_samples": raw_samples, "disk_bytes": size, "health": health}
    with open(a.out, "w") as f:
        json.dump(out, f, indent=1)
    open(a.out + ".done", "w").write("ok\n")


if __name__ == "__main__":
    main()
