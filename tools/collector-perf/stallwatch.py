#!/usr/bin/env python3
"""Сторожевая проба пауз vCPU/планировщика (collector-perf, решение координатора 14.11): поток спит 20 мс и меряет перерасход сна. Перерасход > 100 мс — строка события
(время, перерасход, прирост steal в /proc/stat за этот промежуток); раз в час — итоговая строка (число > 50 / 100 / 300 мс, максимум, число проверок).
Файл /data/stallwatch/stallwatch.jsonl. Нагрузка — около 50 пробуждений в секунду (≈ 0,002 ядра). Сравнение «до/после» перехода NIC на 2 очереди — по числу событий > 100 мс в сутки."""
import json, os, time

OUT = "/data/stallwatch/stallwatch.jsonl"
PERIOD = 0.02
os.makedirs(os.path.dirname(OUT), exist_ok=True)


def steal():
    with open("/proc/stat") as f:
        return int(f.readline().split()[8])


def emit(o):
    with open(OUT, "a") as f:
        f.write(json.dumps(o) + "\n")


cnt = {"n": 0, "gt50": 0, "gt100": 0, "gt300": 0, "max_ms": 0.0}
hour = int(time.time() // 3600)
t_prev = time.monotonic()
s_prev = steal()
emit({"ev": "start", "t": time.time(), "period_ms": PERIOD * 1000})
while True:
    time.sleep(PERIOD)
    t = time.monotonic()
    over = (t - t_prev - PERIOD) * 1000
    t_prev = t
    cnt["n"] += 1
    cnt["max_ms"] = max(cnt["max_ms"], over)
    if over > 50:
        cnt["gt50"] += 1
    if over > 300:
        cnt["gt300"] += 1
    if over > 100:
        cnt["gt100"] += 1
        s = steal()
        emit({"ev": "stall", "t": time.time(), "over_ms": round(over, 1), "steal_jiffies": s - s_prev})
    s_prev = steal() if over > 100 else s_prev
    h = int(time.time() // 3600)
    if h != hour:
        emit({"ev": "hour", "t": time.time(), "hour_utc": hour * 3600, **{k: (round(v, 1) if isinstance(v, float) else v) for k, v in cnt.items()}})
        cnt = {"n": 0, "gt50": 0, "gt100": 0, "gt300": 0, "max_ms": 0.0}
        hour = h
