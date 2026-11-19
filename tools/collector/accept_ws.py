#!/usr/bin/env python3
"""Общая приёмка WS-коллекторов G-022 (В-142; В-141: окно 3 минуты) по записи за окно: разрывы/таймауты/переподключения, лаги, порядок и полнота по u/a, дубли, размер, health.json.
Запуск: accept_ws.py --exchange binance --root /data/bnws --since-ms <старт окна> --until-ms <конец> --out /data/bnws/accept.json
"""
import argparse
import glob
import importlib
import io
import json
import os
import sys

import zstandard
from collections import Counter

sys.path.insert(0, os.path.dirname(__file__))
from wscore import codec, stats  # noqa: E402


def read_lines(path):
    """Строки файла потоком (сутки не помещаются в память). Закрытый файл — целиком; открытый — без неполной последней строки (запись могла оборваться)."""
    with open(path, "rb") as f:
        prev = None
        for ln in io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(f, read_across_frames=True), encoding="utf-8", errors="replace", newline="\n"):
            if prev is not None:
                yield prev
            prev = ln
        if prev is not None and prev.endswith("\n"):
            yield prev


def count_percentile(cnt, p):
    """Тот же перцентиль, что stats.percentile (xs[int(n·p)] отсортированного), но по счётчику значений: память не растёт с числом записей."""
    n = sum(cnt.values())
    if not n:
        return None
    idx, seen = min(n - 1, int(n * p)), 0
    for v in sorted(cnt):
        seen += cnt[v]
        if seen > idx:
            return v


class Stream:
    """Поток типа на диске по всем символам и шардам: лаг записи (приём − время биржи) — счётчик по мс и по 10-с корзинам; занятые мс окна — для паузы между соседними записями."""

    def __init__(self, since_ms, until_ms):
        self.since, self.n, self.lags, self.buckets = since_ms, 0, Counter(), {}
        self.busy = bytearray(until_ms - since_ms + 1)

    def add(self, rx_ms, lag):
        self.n += 1
        self.lags[lag] += 1
        self.buckets.setdefault(rx_ms // 10_000, Counter())[lag] += 1
        self.busy[rx_ms - self.since] = 1

    def summary(self):
        p50s = [count_percentile(c, 0.5) for c in self.buckets.values()]
        gaps = self.busy.split(b"\x01")[1:-1]  # без пустых краёв окна: пауза — между двумя записями
        return {"n": self.n, "lag_ms": {"p50": count_percentile(self.lags, 0.5), "p99": count_percentile(self.lags, 0.99), "max": max(self.lags, default=None)},
                "lag_p50_10s_max": max(p50s, default=None), "max_pause_ms": max(map(len, gaps)) + 1 if gaps else None}


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
                     "lag_p50_ms_median": stats.percentile(lag50, 0.5), "lag_p50_ms_max": max(lag50, default=None),
                     "lag_p99_ms_max": max((r["lag_p99_ms"] for r in rows if r["lag_p99_ms"] is not None), default=None),
                     "cpu_frac_max": max((r["cpu_frac"] for r in rows), default=None)}
    ad = importlib.import_module(f"{a.exchange}_ws").Adapter()
    rec, gaps, size = {"b": 0, "t": 0, "m": 0, "g": 0, "c": 0, "r": 0}, {"book_u_not_increasing": 0, "trade_a_not_increasing": 0, "trade_a_gaps": 0, "trade_a_gap_ids": 0, "mark_E_not_increasing": 0}, 0
    pings, raw_samples, last_a = [], [], {}  # last_a: символ -> последний a сделки по файлам в порядке времени (полнота потока — подряд идущие id)
    stream = {t: Stream(a.since_ms, a.until_ms) for t in "btm"}  # поток на диске после слияния путей (что получит читатель)
    for p in sorted(glob.glob(os.path.join(a.root, "20*", "*.tsv.zst"))):
        if os.path.getmtime(p) * 1000 < a.since_ms:
            continue  # файл закончен до окна (прежний запуск, возможно прежнего формата): записей окна в нём нет
        size += os.path.getsize(p)
        last = {}  # (тип, символ) -> ключ порядка: писатель снимает дубли, поток символа строго растёт (В-142)
        for r in codec.decode(read_lines(p)):
            if not (a.since_ms * 1_000_000 <= r["rx_ns"] <= a.until_ms * 1_000_000):
                continue
            rec[r["t"]] += 1
            if r["t"] == "g":
                pings.append(r["rtt_us"] / 1000)
            elif r["t"] == "r":
                if len(raw_samples) < 5:
                    raw_samples.append(r["frame"][:200])
            elif r["t"] in ("b", "t", "m"):
                stream[r["t"]].add(r["rx_ns"] // 1_000_000, r["rx_ns"] // 1_000_000 - r["E"])
                key, name = (r["u"], "book_u_not_increasing") if r["t"] == "b" else (r["a"], "trade_a_not_increasing") if r["t"] == "t" else (r["E"], "mark_E_not_increasing")
                if (r["t"], r["s"]) in last and key <= last[(r["t"], r["s"])]:
                    gaps[name] += 1
                last[(r["t"], r["s"])] = key
                if r["t"] == "t":
                    x = last_a.get(r["s"])
                    if getattr(ad, "trade_ids_consecutive", False) and x is not None and r["a"] > x + 1:
                        gaps["trade_a_gaps"] += 1
                        gaps["trade_a_gap_ids"] += r["a"] - x - 1
                    last_a[r["s"]] = max(r["a"], x or 0)
    disk = {t: v.summary() for t, v in stream.items()}
    health = json.load(open(os.environ.get("HEALTH", ad.health_default)))
    dials = sorted(e["ts_ms"] for k in ("connected", "path_rejected_rtt") for e in ev.get(k, []))  # каждый TCP connect = connected или path_rejected_rtt
    dials_5min = max((sum(1 for t in dials if t0 <= t < t0 + 300_000) for t0 in dials), default=0)  # максимум подключений в любом окне 5 мин (лимит биржи 300)
    out = {"window_ms": [a.since_ms, a.until_ms], "dials": len(dials), "dials_5min_max": dials_5min, "events": {k: len(v) for k, v in ev.items()}, "events_detail": {k: v[:20] for k, v in ev.items() if k != "connected" and k != "subscribed"},
           "disk_stream": disk, "shards": st, "records": rec, "sequence_checks": gaps, "ping_ms": {"n": len(pings), "p50": stats.percentile(pings, 0.5), "p99": stats.percentile(pings, 0.99), "max": max(pings, default=None)},
           "raw_samples": raw_samples, "disk_bytes": size, "health": health}
    with open(a.out, "w") as f:
        json.dump(out, f, indent=1)
    open(a.out + ".done", "w").write("ok\n")


if __name__ == "__main__":
    main()
