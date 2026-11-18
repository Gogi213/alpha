"""TK-609: замер скорости `alpha-feat` против `tk594_feat.py` на равных: те же монеты, окно, число потоков, nice, чередование A/B/A/B.

Меряет стену и ЦП-секунды (user+sys всех дочерних, включая `unzip`) каждого прогона и load средней за прогон.
Режим flow. Монеты — равномерная выборка по списку (сортировка по числу суток чтения), `--ncoins N`.
Использование: rustfeat_bench.py <eps.parquet> --base-date D --trades <каталог aggtrades> --bin <alpha-feat> --ncoins 20 --procs 2 --reps 2
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import shlex
import subprocess
import sys
import tempfile
import time

import pandas as pd

PY_FEAT = "/data/tk594/py/tk594_feat.py"
DAY_MS = 86_400_000


def cpu_children() -> float:
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    return ru.ru_utime + ru.ru_stime


def timed(cmd, env, wrap):
    """Прогон cmd: (стена с, ЦП с детей, load в начале, load в конце)."""
    l0 = os.getloadavg()[0]
    c0, t0 = cpu_children(), time.time()
    r = subprocess.run(wrap + cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    wall, cpu = time.time() - t0, cpu_children() - c0
    if r.returncode:
        sys.exit(f"rc={r.returncode}: {' '.join(cmd)}\n{r.stderr[-800:]}")
    return {"wall": round(wall, 1), "cpu": round(cpu, 1), "load0": l0, "load1": os.getloadavg()[0],
            "tail": (r.stdout.strip().splitlines() or [""])[-1][:600]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("eps")
    ap.add_argument("--base-date", required=True)
    ap.add_argument("--trades", required=True)
    ap.add_argument("--bin", required=True, help="alpha-feat (можно несколько через запятую; подпись — имя файла)")
    ap.add_argument("--bin-args", action="append", default=[],
                    help="доп. аргументы бинаря (shlex); повторяется по числу --bin по порядку, пусто — без аргументов")
    ap.add_argument("--ncoins", type=int, default=20)
    ap.add_argument("--procs", type=int, default=2)
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--nice", type=int, default=0)
    ap.add_argument("--no-python", action="store_true")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    e = pd.read_parquet(a.eps, columns=["ep_id", "sym", "side", "sig_ms", "day", "turn24h"])
    span = e.assign(d=e.sig_ms // DAY_MS).groupby("sym").d.agg(lambda s: s.max() - s.min() + 9).sort_values(kind="stable")
    pick = [span.index[int(i * len(span) / a.ncoins)] for i in range(a.ncoins)] if a.ncoins < len(span) else list(span.index)
    e = e[e.sym.isin(pick)]
    days_read = int(span[pick].sum())
    tmp = tempfile.mkdtemp(prefix="rustbench-", dir=os.path.dirname(os.path.abspath(a.out)) if a.out else None)
    eps_csv = os.path.join(tmp, "eps.csv")
    e[["ep_id", "sym", "side", "sig_ms", "turn24h"]].to_csv(eps_csv, index=False)
    coins = ",".join(pick)
    env = dict(os.environ, TK594_TRADES=a.trades)
    wrap = ["nice", "-n", str(a.nice)] if a.nice else []
    bins = a.bin.split(",")
    bargs = [shlex.split(x) for x in a.bin_args] + [[]] * len(bins)
    runs = []
    for rep in range(a.reps):
        if not a.no_python:
            r = timed([sys.executable, PY_FEAT, a.eps, os.path.join(tmp, "py.parquet"), "--mode", "flow", "--base-date", a.base_date,
                       "--procs", str(a.procs), "--coins", coins], env, wrap)
            runs.append({"rep": rep, "engine": "python", **r})
            print(json.dumps(runs[-1]), flush=True)
        for bi, b in enumerate(bins):
            r = timed([b, "flow", "--eps", eps_csv, "--out", os.path.join(tmp, "rs.csv"), "--base-date", a.base_date, "--threads", str(a.procs),
                       "--coins", coins, *bargs[bi]], env, wrap)
            runs.append({"rep": rep, "engine": "rust:" + os.path.basename(b) + ("+args" if bargs[bi] else ""), **r})
            print(json.dumps(runs[-1]), flush=True)
    summ = {"coins": len(pick), "episodes": int(len(e)), "days_read": days_read, "procs": a.procs, "nice": a.nice, "runs": runs}
    if a.out:
        json.dump(summ, open(a.out, "w"), indent=1)
    for f in os.listdir(tmp):
        os.remove(os.path.join(tmp, f))
    os.rmdir(tmp)
    print(json.dumps({k: v for k, v in summ.items() if k != "runs"}))


if __name__ == "__main__":
    main()
