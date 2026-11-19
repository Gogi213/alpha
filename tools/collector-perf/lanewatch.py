#!/usr/bin/env python3
"""Вклад линий по шардам на 7 суток (collector-perf, решение координатора 14.11): раз в 10 мин снимок счётчиков `shards[].lanes` (first / delivered / unique) из health четырёх
боевых коллекторов в /data/lanes/lanes.jsonl. Счётчики в процессе накопительные и обнуляются при перезапуске — отчёт `lanes_report.py` складывает приращения по запускам.
`unique` — личности, принесённые ТОЛЬКО этой линией (считается при истечении поколения, 2–4 мин). Решение по «запасной» линии — владельцу после 7 суток, по этим числам."""
import json, os, time

OUT = "/data/lanes/lanes.jsonl"
HEALTH = {
    "binance": "/data/turbo-probe/soak-binance/health.json",
    "bybit": "/data/turbo-probe/soak-bybit/health.json",
    "okx": "/data/collect/okx/health.json",
    "bingx": "/data/collect/bingx/health.json",
}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
while True:
    now = time.time()
    with open(OUT, "a") as f:
        for ex, p in HEALTH.items():
            try:
                h = json.load(open(p))
                for s in h["shards"]:
                    l = s["lanes"]
                    f.write(json.dumps({"t": round(now), "ex": ex, "shard": s["shard"], "start": h["started_ms"], "first": l["first"][:2], "delivered": l["delivered"][:2], "unique": l["unique"][:2]}) + "\n")
            except Exception as e:
                f.write(json.dumps({"t": round(now), "ex": ex, "error": str(e)}) + "\n")
    time.sleep(600)
