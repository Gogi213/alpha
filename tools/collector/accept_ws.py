#!/usr/bin/env python3
"""Общая приёмка WS-коллекторов G-022 (В-142; В-141: окно 3 минуты) по записи за окно: разрывы/таймауты/переподключения, лаги, порядок и полнота по u/a, дубли, размер, health.json.
Запуск: accept_ws.py --exchange binance --root /data/bnws --since-ms <старт окна> --until-ms <конец> --out /data/bnws/accept.json
"""
import argparse
import glob
import importlib
import json
import os
import sys

import zstandard

sys.path.insert(0, os.path.dirname(__file__))
import wscore as bw  # noqa: E402


def read_file(path):
    """Закрытый файл (есть .ok) — целиком; открытый — без последней строки (запись могла оборваться на середине)."""
    with open(path, "rb") as f:
        text = zstandard.ZstdDecompressor().stream_reader(f, read_across_frames=True).read().decode(errors="replace")
    return text if text.endswith("\n") else text[: text.rfind("\n") + 1]  # открытый файл (или обрыв записи) — без неполной последней строки


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exchange", required=True)
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
    ad = importlib.import_module(f"{a.exchange}_ws").Adapter()
    rec, gaps, size = {"b": 0, "t": 0, "m": 0, "g": 0, "c": 0, "r": 0}, {"book_u_not_increasing": 0, "trade_a_not_increasing": 0, "trade_a_gaps": 0, "trade_a_gap_ids": 0, "mark_E_not_increasing": 0}, 0
    pings, raw_samples, ids = [], [], {}
    for p in sorted(glob.glob(os.path.join(a.root, "20*", "*.tsv.zst"))):
        if os.path.getmtime(p) * 1000 < a.since_ms:
            continue  # файл закончен до окна (прежний запуск, возможно прежнего формата): записей окна в нём нет
        size += os.path.getsize(p)
        last = {}  # (тип, символ) -> ключ порядка: писатель снимает дубли, поток символа строго растёт (В-142)
        for r in bw.decode(read_file(p)):
            if not (a.since_ms * 1_000_000 <= r["rx_ns"] <= a.until_ms * 1_000_000):
                continue
            rec[r["t"]] += 1
            if r["t"] == "g":
                pings.append(r["rtt_us"] / 1000)
            elif r["t"] == "r":
                if len(raw_samples) < 5:
                    raw_samples.append(r["frame"][:200])
            elif r["t"] in ("b", "t", "m"):
                key, name = (r["u"], "book_u_not_increasing") if r["t"] == "b" else (r["a"], "trade_a_not_increasing") if r["t"] == "t" else (r["E"], "mark_E_not_increasing")
                if (r["t"], r["s"]) in last and key <= last[(r["t"], r["s"])]:
                    gaps[name] += 1
                last[(r["t"], r["s"])] = key
                if r["t"] == "t":
                    ids.setdefault(r["s"], set()).add(r["a"])
    if getattr(ad, "trade_ids_consecutive", False):
        for seen in ids.values():  # полнота потока сделок по a (подряд идущие id на символ) — по объединению файлов окна
            xs = sorted(seen)
            for x, y in zip(xs, xs[1:]):
                if y != x + 1:
                    gaps["trade_a_gaps"] += 1
                    gaps["trade_a_gap_ids"] += y - x - 1
    health = json.load(open(os.environ.get("HEALTH", ad.health_default)))
    out = {"window_ms": [a.since_ms, a.until_ms], "events": {k: len(v) for k, v in ev.items()}, "events_detail": {k: v[:20] for k, v in ev.items() if k != "connected" and k != "subscribed"},
           "shards": st, "records": rec, "sequence_checks": gaps, "ping_ms": {"n": len(pings), "p50": bw.percentile(pings, 0.5), "p99": bw.percentile(pings, 0.99), "max": max(pings, default=None)},
           "raw_samples": raw_samples, "disk_bytes": size, "health": health}
    with open(a.out, "w") as f:
        json.dump(out, f, indent=1)
    open(a.out + ".done", "w").write("ok\n")


if __name__ == "__main__":
    main()
