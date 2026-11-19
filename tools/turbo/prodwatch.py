#!/usr/bin/env python3
"""G-022 pool-fix: нагрузка боевого процесса рядом с окнами копии (preferences п. 23: сравниваются только результаты при равных условиях).

  prodwatch.py --unit collect-binance.service --health /data/turbo-probe/soak-binance/health.json --out prodwatch.jsonl --seconds 3000 [--period 10]

Каждые `--period` с пишет строку: ts_ms, usage_usec юнита (cgroup v2), кадров в секунду по health (`shards[].msgs_10s` / 10), load1, cpu.pressure avg10, процесс health (cpu_cores,
lag_in p99, queue_lag p99). Окно копии — по `t0-ms.txt` её каталога: среднее по строкам внутри [t0 + 10 с, t0 + 175 с] считает `prodwin.py`.
Читает только /sys/fs/cgroup, /proc и health.json боевого процесса; ничего, кроме --out, не пишет.
"""
import argparse
import json
import time

CG = "/sys/fs/cgroup/system.slice"


def usage_usec(unit):
    try:
        for ln in open(f"{CG}/{unit}/cpu.stat"):
            if ln.startswith("usage_usec"):
                return int(ln.split()[1])
    except OSError:
        pass
    return None


def psi_avg10():
    try:
        for ln in open("/proc/pressure/cpu"):
            if ln.startswith("some"):
                return float(ln.split("avg10=")[1].split()[0])
    except (OSError, IndexError, ValueError):
        pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--unit", required=True)
    ap.add_argument("--health", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=int, required=True)
    ap.add_argument("--period", type=int, default=10)
    a = ap.parse_args()
    end = time.time() + a.seconds
    with open(a.out, "a") as f:
        while time.time() < end:
            row = {"ts_ms": int(time.time() * 1000), "usage_usec": usage_usec(a.unit), "load1": float(open("/proc/loadavg").read().split()[0]), "psi_cpu_avg10": psi_avg10()}
            try:
                h = json.load(open(a.health))
                row["frames_s"] = round(sum(s.get("msgs_10s", 0) for s in h.get("shards", [])) / 10)
                p = h.get("process") or {}
                bp = h.get("backpressure") or {}
                row.update({"cpu_cores": p.get("cpu_cores"), "lag_in_p99_ms": (h.get("lag_in_ms") or {}).get("p99"),
                            "queue_lag_p99_ms": (bp.get("queue_lag_ms") or {}).get("p99"), "connections": (h.get("ws") or {}).get("connections"),
                            "symbols": (h.get("symbols") or {}).get("subscribed")})
            except (OSError, ValueError):
                pass
            f.write(json.dumps(row) + "\n")
            f.flush()
            time.sleep(a.period)


if __name__ == "__main__":
    main()
