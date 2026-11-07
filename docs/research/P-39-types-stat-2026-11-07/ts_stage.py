#!/usr/bin/env python3
"""Стадия 1 (Deck): боковые панели оборота и ряды s, b, sigma, r6, c для 5 830 пар крипты за 35 суток (2465..2499). Кэш - <out>/cache, <out>/turn (временные, удаляются после счёта).

  python ts_stage.py turn   [--threads 3]
  python ts_stage.py series [--threads 3] [--cells N]
  python ts_stage.py verify                    сверка b, sigma, r6, c первых 14 суток с кэшем формирования ~/xdl9/c2/out/calib/cache (float32, только чтение)
"""
import argparse
import json
import multiprocessing as mp
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ts_lib as T      # noqa: E402

_W = {}


def _init(nice):
    os.nice(nice)
    _W["world"] = T.make_world()


def _turn_task(leg):
    ex, sym = leg
    src = os.path.join(T.RAW_ROOT, ex, "candles", T.W.safe_name(sym) + ".csv.gz")
    dst = os.path.join(T.OUT, "turn", ex, T.W.safe_name(sym) + ".npy")
    if not os.path.isfile(src) or os.path.isfile(dst):
        return 0
    a = T.load_turnover(src, T.D0, T.D_W1)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    np.save(dst, a.astype(np.float32))
    return 1


def _series_task(args):
    cell, pairs = args
    t0 = time.time()
    cfg = T.L.Cfg(**T.CFG_OFFICIAL)
    metas = T.W.series_cell(_W["world"], cell, pairs, T.D0, T.D_W1, cfg, T.OUT)
    return cell, len(pairs), time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["turn", "series", "verify"])
    ap.add_argument("--threads", type=int, default=3)
    ap.add_argument("--cells", type=int, default=0)
    a = ap.parse_args()
    world = T.make_world()
    pairs = T.crypto_pairs(world)
    print("пар крипты %d, активов %d" % (len(pairs), len({p["key"] for p in pairs})))
    T.log_read("stage %s: пары крипты %d, сутки %d..%d, читатель без защиты (post hoc)" % (a.cmd, len(pairs), T.D0, T.D_W1))
    if a.cmd == "turn":
        legs = sorted({l for p in pairs for l in p["legs"]})
        t0 = time.time()
        ctx = mp.get_context("fork")
        with ctx.Pool(a.threads, initializer=_init, initargs=(10,)) as pool:
            n = sum(pool.imap_unordered(_turn_task, legs, chunksize=20))
        print("turn: ног %d, записано %d, %.0f с" % (len(legs), n, time.time() - t0))
    elif a.cmd == "series":
        cells = {}
        for p in pairs:
            cells.setdefault(p["cell"], []).append(p)
        tasks = sorted(cells.items(), key=lambda kv: -len(kv[1]))
        if a.cells:
            tasks = tasks[-a.cells:]
        t0 = time.time()
        ctx = mp.get_context("fork")
        with ctx.Pool(a.threads, initializer=_init, initargs=(10,)) as pool:
            for cell, n, dt in pool.imap_unordered(_series_task, tasks, chunksize=1):
                print("  %-22s пар %4d  %.0f с  (всего %.0f с)" % (cell, n, dt, time.time() - t0), flush=True)
        print("series: ячеек %d, %.0f с" % (len(tasks), time.time() - t0))
    else:
        ref = os.path.expanduser("~/xdl9/c2/out/calib/cache/L1440")
        mine = os.path.join(T.OUT, "cache", "L1440")
        n14 = (T.D_F1 - T.D0 + 1) * T.MIN_DAY
        bad, n = 0, 0
        mx = 0.0
        import random
        rnd = random.Random(37)
        pp = [p for p in pairs if os.path.isfile(os.path.join(mine, "pairs", p["pid"] + ".npy")) and os.path.isfile(os.path.join(ref, "pairs", p["pid"] + ".npy"))]
        for p in rnd.sample(pp, min(300, len(pp))):
            x = np.load(os.path.join(mine, "pairs", p["pid"] + ".npy"), mmap_mode="r")[:, :n14]
            y = np.load(os.path.join(ref, "pairs", p["pid"] + ".npy"), mmap_mode="r")[:, :n14]
            same = (x == y) | (np.isnan(x) & np.isnan(y))
            n += 1
            if not same.all():
                bad += 1
                mx = max(mx, float(np.nanmax(np.abs(x - y))))
        cb = 0
        for cell in sorted({p["cell"] for p in pp}):
            f = T.W.safe(cell) + ".npy"
            if not (os.path.isfile(os.path.join(mine, "cells", f)) and os.path.isfile(os.path.join(ref, "cells", f))):
                continue
            x = np.load(os.path.join(mine, "cells", f), mmap_mode="r")[:, :n14]
            y = np.load(os.path.join(ref, "cells", f), mmap_mode="r")[:, :n14]
            if not (((x == y) | (np.isnan(x) & np.isnan(y))).all()):
                cb += 1
        print("verify: пар сверено %d, расхождений %d (макс |Δ| %.3g); ячеек c с расхождением %d" % (n, bad, mx, cb))


if __name__ == "__main__":
    main()
