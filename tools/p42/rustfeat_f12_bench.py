"""TK-611: скорость f12 на равных — `alpha-feat f12` против `retitr_ftab.build_episodes` (Python): те же монеты (один отфильтрованный sigsx), окно, число
потоков, nice, чередование A/B/A/B. Меряет стену и ЦП-секунды (user+sys всех дочерних, включая `unzip`) каждого прогона и load среднюю.

  python3 rustfeat_f12_bench.py --sigsx <csv> --side long|short --cache C --trades T --base-date D --bin <alpha-feat>[,<alpha-feat>…] [--variant NAME=ARGS …] --ncoins 20 --procs 2 --reps 2
Монеты — равномерная выборка по списку, отсортированному по числу эпизодов; `--bin-args` — доп. аргументы бинаря (одна строка).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import resource
import shlex
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def cpu_children() -> float:
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    return ru.ru_utime + ru.ru_stime


def timed(cmd, wrap):
    l0 = os.getloadavg()[0]
    c0, t0 = cpu_children(), time.time()
    r = subprocess.run(wrap + cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    wall, cpu = time.time() - t0, cpu_children() - c0
    if r.returncode:
        sys.exit(f"rc={r.returncode}: {' '.join(cmd)}\n{r.stderr[-800:]}")
    return {"wall": round(wall, 1), "cpu": round(cpu, 1), "load0": l0, "load1": os.getloadavg()[0]}, r.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sigsx", required=True)
    ap.add_argument("--side", required=True, choices=["long", "short"])
    ap.add_argument("--cache", required=True)
    ap.add_argument("--trades", required=True)
    ap.add_argument("--base-date", required=True)
    ap.add_argument("--bin", required=True)
    ap.add_argument("--bin-args", default="")
    ap.add_argument("--variant", action="append", default=[], help="NAME=ARGS: ещё один прогон первого бинаря с другими аргументами (например store=--store D --store-cap-gb 0)")
    ap.add_argument("--ncoins", type=int, default=20)
    ap.add_argument("--procs", type=int, default=2)
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--nice", type=int, default=0)
    ap.add_argument("--no-python", action="store_true")
    ap.add_argument("--keep", default="", help="каталог для результатов (CSV Rust, parquet Python, отфильтрованный sigsx) — для ворот")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    with open(a.sigsx, encoding="utf-8", newline="") as f:
        rd = csv.reader(f)
        head = next(rd)
        rows = list(rd)
    cnt: dict[str, int] = {}
    for r in rows:
        cnt[r[0]] = cnt.get(r[0], 0) + 1
    coins = sorted(cnt, key=lambda s: (cnt[s], s))
    pick = [coins[int(i * len(coins) / a.ncoins)] for i in range(a.ncoins)] if a.ncoins < len(coins) else coins
    tmp = a.keep or tempfile.mkdtemp(prefix="f12bench-")
    os.makedirs(tmp, exist_ok=True)
    sx = os.path.join(tmp, "sigsx-sel.csv")
    chosen = set(pick)
    with open(sx, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(head)
        w.writerows(r for r in rows if r[0] in chosen)
    n_ep = sum(cnt[c] for c in pick)
    wrap = ["nice", "-n", str(a.nice)] if a.nice else []
    bins = a.bin.split(",")
    variants = [(os.path.basename(b), b, shlex.split(a.bin_args)) for b in bins]
    for v in a.variant:
        name, _, vargs = v.partition("=")
        variants.append((name, bins[0], shlex.split(vargs)))
    runs = []
    for rep in range(a.reps):
        if not a.no_python:
            r, _ = timed([sys.executable, os.path.join(HERE, "rustfeat_f12_pyref.py"), "--sigsx", sx, "--side", a.side, "--out", os.path.join(tmp, "py.parquet"),
                          "--cache", a.cache, "--trades", a.trades, "--base-date", a.base_date, "--procs", str(a.procs)], wrap)
            runs.append({"rep": rep, "engine": "python", **r})
            print(json.dumps(runs[-1]), flush=True)
        for name, b, bargs in variants:
            r, out = timed([b, "f12", "--sigsx", sx, "--side", a.side, "--out", os.path.join(tmp, "rs-%s.csv" % name), "--cache", a.cache,
                            "--trades", a.trades, "--base-date", a.base_date, "--threads", str(a.procs)] + bargs, wrap)
            runs.append({"rep": rep, "engine": "rust:" + name, **r, "summary": out.strip().splitlines()[-1] if out.strip() else ""})
            print(json.dumps(runs[-1]), flush=True)
    summ = {"coins": len(pick), "episodes": n_ep, "procs": a.procs, "nice": a.nice, "runs": runs}
    by: dict[str, list] = {}
    for r in runs:
        by.setdefault(r["engine"], []).append(r)
    summ["mean"] = {e: {"wall": round(sum(x["wall"] for x in v) / len(v), 1), "cpu": round(sum(x["cpu"] for x in v) / len(v), 1)} for e, v in by.items()}
    if "python" in summ["mean"]:
        py = summ["mean"]["python"]
        summ["speedup"] = {e: {"wall": round(py["wall"] / m["wall"], 2), "cpu": round(py["cpu"] / m["cpu"], 2)} for e, m in summ["mean"].items() if e != "python"}
    print(json.dumps(summ, ensure_ascii=False))
    if a.out:
        json.dump(summ, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
