#!/usr/bin/env python3
"""TK-493, П-38 r2 §7.1 K2: правило R-L выбора окна базиса L из {1440, 4320} (записано в протоколе ДО счёта).

Правило (дословно §7.1 K2): на сутках 2468…2478 (в Pi_train - 2468…2471), где доступны полные истории обоих окон, для каждой пары считается внепробный разброс MAD(d_L) на
следующих сутках; берётся медиана по парам отношения MAD(d_4320) / MAD(d_1440); L = 4320, только если медиана отношения < 1 одновременно в классах крипта и акции+ETF, иначе L = 1440.

Прочтение реализации (решение записано здесь и в отчёте калибровки, R-K2):
  d_L,t = s_t - b_L,t, где b_L,t - базис по минутам [t-L, t) (причинный, текущая минута не входит) - поэтому d_L на минуте t внепробен относительно собственного базиса;
  разброс пары - MAD = медиана |d - медиана d| по минутам суток первой полной истории (2468) … last_day, на ОДНОМ и том же наборе минут, где определены d_1440 и d_4320
  (так сравнение не зависит от разной плотности допущенных минут); пара входит, если таких минут >= 240 и MAD(d_1440) > 0;
  отношение пары = MAD(d_4320) / MAD(d_1440); класс - медиана отношений по парам основного ряда класса.
Кэши рядов обоих L строятся conv_run.py series (--L 1440 и --L 4320, один и тот же --mode / --last-day); панели читаются через conv_guard.
  python conv_k2.py --out DIR --mode calib|train --last-day 2478 [--threads 6] [--json PATH]
"""
import argparse
import json
import multiprocessing as mp
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conv_guard as G      # noqa: E402
import conv_lib as L        # noqa: E402
import conv_world as W      # noqa: E402

MIN_DAY = 1440
FIRST_FULL_DAY = 2468       # сутки, с которых доступна полная история 4320 минут при старте данных 2465
MIN_OBS = 240
_CTX = {}


def mad(x):
    m = np.median(x)
    return float(np.median(np.abs(x - m)))


def pair_ratio(s, b1, b4, first_idx, min_obs=MIN_OBS):
    """-> (n, MAD1440, MAD4320, отношение) либо None, если пара не входит."""
    d1 = s[first_idx:] - b1[first_idx:]
    d4 = s[first_idx:] - b4[first_idx:]
    ok = np.isfinite(d1) & np.isfinite(d4)
    n = int(ok.sum())
    if n < min_obs:
        return None
    m1, m4 = mad(d1[ok]), mad(d4[ok])
    if not m1 > 0:
        return None
    return n, m1, m4, m4 / m1


def _task(pids):
    cx = _CTX
    world, out, d0, d1 = cx["world"], cx["out"], cx["d0"], cx["d1"]
    pd1 = W.cache_dirs(out, 1440)[0]
    pd4 = W.cache_dirs(out, 4320)[0]
    first_idx = (FIRST_FULL_DAY - d0) * MIN_DAY
    N = (d1 - d0 + 1) * MIN_DAY
    res = []
    for p in pids:
        oA, cA, oB, cB, sess = world.load_pair_raw(p, d0, d1)
        s, _ = L.spread_series(cA, cB)
        b1 = np.load(os.path.join(pd1, p["pid"] + ".npy"), mmap_mode="r")[0, :N].astype(np.float64)
        b4 = np.load(os.path.join(pd4, p["pid"] + ".npy"), mmap_mode="r")[0, :N].astype(np.float64)
        r = pair_ratio(s, b1, b4, first_idx)
        res.append((p["pid"], p["cls"], p["cell"], r))
    return res


def _init(root, mode, params, script, out, d0, d1):
    G.configure(go_window=False, script_path=script, params_paths=params, mode=mode, require_k0=True)
    _CTX.update(world=W.World(root), out=out, d0=d0, d1=d1)


def decide(by_class):
    """R-L: L = 4320 только если медиана отношения < 1 одновременно в крипте и акциях+ETF."""
    c, e = by_class.get("crypto", {}).get("median"), by_class.get("eq", {}).get("median")
    return 4320 if (c is not None and e is not None and c < 1.0 and e < 1.0) else 1440


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=G.HOME)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", default="calib", choices=["calib", "train"])
    ap.add_argument("--last-day", type=int, required=True)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--json", default=None)
    ap.add_argument("--params", default=None)
    W.add_gate_args(ap)
    a = ap.parse_args(argv)
    W.apply_gate_args(a)
    script = os.path.join(HERE, "conv_run.py")
    params = (a.params or os.path.join(HERE, "P-38-params-2026-11-07.csv"), None)
    if not os.path.isfile(params[0]):
        params = (os.path.join(G.HOME, "audit3", "conv", "code", "P-38-params-2026-11-07.csv"), None)
    G.configure(go_window=False, script_path=script, params_paths=params, mode=a.mode, require_k0=True)
    G.check_days(a.last_day, what="k2")
    G.require_k0()
    G.ensure_params_committed()
    world = W.World(a.root)
    pairs = [p for p in world.universe() if world.in_main_series(p)]
    out = os.path.join(a.out, a.mode)
    cells = {}
    for p in pairs:
        cells.setdefault(p["cell"], []).append(p)
    tasks = [v for _, v in sorted(cells.items())]
    ctx = mp.get_context("fork")
    with ctx.Pool(a.threads, initializer=_init, initargs=(a.root, a.mode, params, script, out, G.D_FORM0, a.last_day)) as pool:
        parts = list(pool.imap(_task, tasks, chunksize=1))
    rows = [r for part in parts for r in part]
    by_class = {}
    for cls in ("crypto", "eq"):
        rr = [r[3][3] for r in rows if r[1] == cls and r[3] is not None]
        if rr:
            q = np.quantile(rr, [0.25, 0.5, 0.75])
            by_class[cls] = dict(pairs_in=len(rr), pairs_total=sum(1 for r in rows if r[1] == cls), median=float(q[1]), q25=float(q[0]), q75=float(q[2]),
                                 share_below_1=float(np.mean(np.array(rr) < 1.0)))
    L_sel = decide(by_class)
    res = dict(mode=a.mode, last_day=a.last_day, first_full_day=FIRST_FULL_DAY, by_class=by_class, L=L_sel,
               rule="L = 4320 только если медиана отношения MAD(d_4320)/MAD(d_1440) < 1 одновременно в крипте и акциях+ETF, иначе 1440")
    print(json.dumps(res, ensure_ascii=False, indent=1))
    if a.json:
        os.makedirs(os.path.dirname(a.json), exist_ok=True)
        json.dump(res, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
