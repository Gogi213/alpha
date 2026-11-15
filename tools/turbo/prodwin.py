#!/usr/bin/env python3
"""G-022 pool-fix: нагрузка боевого процесса в окнах копии лестницы `top10_ladder.sh` (условия рядом с числами окна, п. 23).

  prodwin.py <каталог метки>      # читает prodwatch.jsonl и <метка>-n<ступень>-w<k>/t0-ms.txt; печатает строку на окно

Окно измерения — как в `steps.sh`: [t0 + 10 с, t0 + 175 с]. Боевой процесс: ядер по cgroup (usage_usec), кадров/с по health, load1, cpu.pressure avg10, p99 лага прихода и очереди.
"""
import glob
import json
import os
import sys


def main():
    d = sys.argv[1]
    rows = [json.loads(ln) for ln in open(os.path.join(d, "prodwatch.jsonl")) if ln.strip()]
    for f in sorted(glob.glob(os.path.join(d, "*-n*-w*/t0-ms.txt"))):
        w = os.path.basename(os.path.dirname(f))
        t0 = int(open(f).read().strip())
        a, b = t0 + 10_000, t0 + 175_000
        inside = [r for r in rows if a <= r["ts_ms"] <= b]
        if len(inside) < 2:
            print(f"{w}: нет строк prodwatch в окне")
            continue
        first, last = inside[0], inside[-1]
        dt = (last["ts_ms"] - first["ts_ms"]) / 1000
        cores = (last["usage_usec"] - first["usage_usec"]) / 1e6 / dt if first["usage_usec"] is not None and last["usage_usec"] is not None and dt > 0 else None
        mean = lambda k: round(sum(r[k] for r in inside if r.get(k) is not None) / max(1, sum(1 for r in inside if r.get(k) is not None)), 2)
        mx = lambda k: max((r[k] for r in inside if r.get(k) is not None), default=None)
        print(json.dumps({"window": w, "prod_cores": None if cores is None else round(cores, 2), "prod_frames_s": mean("frames_s"), "load1": mean("load1"),
                          "psi_cpu_avg10": mean("psi_cpu_avg10"), "prod_lag_in_p99_ms_max": mx("lag_in_p99_ms"), "prod_queue_lag_p99_ms_max": mx("queue_lag_p99_ms"),
                          "prod_symbols": last.get("symbols"), "samples": len(inside)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
