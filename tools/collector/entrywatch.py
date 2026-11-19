#!/usr/bin/env python3
"""TK-532: сторож пробы входов — печатает строку, когда меняется число эпизодов (поздние записи любого входа, разрыв > gap с), и сводку раз в heartbeat секунд.
Запуск: entrywatch.py DIR [--gap 30] [--heartbeat 1800] [--unit bbws-entryprobe]; завершается, когда юнит пробы остановлен.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time


def late_times(d):
    out = {}
    for p in glob.glob(os.path.join(d, "late-*.jsonl")):
        n = os.path.basename(p)[5:-6]
        out[n] = [json.loads(x)["rx_ms"] for x in open(p) if x.strip()]
    return out


def episodes(late, gap_ms):
    eps, cur = [], None
    for rx in sorted(t for v in late.values() for t in v):
        if cur is None or rx - cur[1] > gap_ms:
            cur = [rx, rx]
            eps.append(cur)
        cur[1] = rx
    return eps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--gap", type=int, default=30)
    ap.add_argument("--heartbeat", type=int, default=1800)
    ap.add_argument("--unit", default="bbws-entryprobe")
    a = ap.parse_args()
    seen, last_hb = -1, 0.0
    while True:
        late = late_times(a.dir)
        eps = episodes(late, a.gap * 1000)
        if len(eps) != seen:
            seen = len(eps)
            if eps:
                t0, t1 = eps[-1]
                per = {n: sum(t0 <= t <= t1 for t in v) for n, v in sorted(late.items())}
                print(f"ЭПИЗОД {len(eps)}: rx_ms {t0}..{t1} ({(t1 - t0) / 1000:.1f} с) поздних записей по входам {per}", flush=True)
        if time.time() - last_hb >= a.heartbeat:
            last_hb = time.time()
            print(f"сводка {time.strftime('%H:%M:%S', time.gmtime())} UTC: эпизодов {len(eps)}, поздних записей по входам {{{', '.join(f'{n}: {len(v)}' for n, v in sorted(late.items()))}}}", flush=True)
        if subprocess.run(["systemctl", "is-active", "--quiet", a.unit]).returncode != 0:
            print(f"юнит {a.unit} остановлен; эпизодов {len(eps)}", flush=True)
            return
        time.sleep(20)


if __name__ == "__main__":
    sys.exit(main())
