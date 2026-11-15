"""Проверка «оценки верха BingX по сделкам» против настоящих снимков bookTicker BingX (все записи, без снятия повторов) на выборке монет.

Запуск:
  python3 bx_snap_check.py parse <монеты.json> <выход> <t0_мс> [PAR]   — сырые book*.tsv.zst BingX → <выход>/<символ>.<метка>.npz (E, T = E − ET, bid, ask); монеты.json = {символ BingX: …}
  python3 bx_snap_check.py check <монеты.json> <каталог снимков> <каталог сделок (bx_trades_parse w/parsed)> <t0> <t1> <выход.pkl>
Сделки сопоставляются со снимками по времени сопоставления T (у снимка T = E − ET, у сделки T).
"""
import glob
import json
import pickle
import subprocess
import sys
import time
from array import array
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import bx_trades as bx  # noqa: E402
from arb_live import days_between  # noqa: E402
from common import ROOTS  # noqa: E402

LOG_LO, LOG_HI, LOG_STEP = -6.0, 2.0, 0.1      # log10 ошибки в %
LB = int(round((LOG_HI - LOG_LO) / LOG_STEP))
EPS = 1e-9
TOUCH_EDGES = np.r_[0, 10.0 ** np.arange(-4, 1.01, 0.25)]   # улучшение цены против касания, %


def _parse_file(job):
    f, want, out, tag, lo, hi = job
    p = subprocess.Popen(["zstd", "-dc", f], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=1 << 22)
    cum, arrs = 0, {}
    for line in p.stdout:
        x = line.split(b"\t")
        cum += int(x[1])
        if x[0] != b"b":
            continue
        s = x[4]
        if s not in want:
            continue
        e = cum // 1_000_000 - int(x[2])
        if e < lo or e >= hi:
            continue
        a = arrs.get(s)
        if a is None:
            a = arrs[s] = (array("q"), array("q"), array("d"), array("d"))
        a[0].append(e)
        a[1].append(e - int(x[3]))
        a[2].append(float(x[6]))
        a[3].append(float(x[8]))
    p.wait()
    for s, a in arrs.items():
        np.savez(Path(out, want[s] + "." + tag + ".npz"), e=np.frombuffer(a[0], "i8"), t=np.frombuffer(a[1], "i8"), bid=np.frombuffer(a[2], "f8"), ask=np.frombuffer(a[3], "f8"))
    return sum(len(a[0]) for a in arrs.values())


def parse(coins, out, t0, par=4):
    Path(out).mkdir(parents=True, exist_ok=True)
    want = {s.encode(): s for s in coins}
    jobs = []
    for day in days_between(t0, int(time.time() * 1000)):
        for f in sorted(glob.glob(f"{ROOTS['bingx']}/{day}/book*.tsv.zst")):
            jobs.append((f, want, out, day + "-" + Path(f).name.removesuffix(".tsv.zst"), t0 - 60_000, 1 << 62))
    with Pool(par) as p:
        n = sum(p.map(_parse_file, jobs, chunksize=1))
    print({"files": len(jobs), "rows": n})


def load_snaps(d, sym, t0, t1):
    fs = sorted(glob.glob(str(Path(d, sym + ".*.npz"))))
    if not fs:
        return None
    parts = [np.load(f) for f in fs]
    a = {k: np.concatenate([p[k] for p in parts]) for k in ("e", "t", "bid", "ask")}
    o = np.lexsort((a["e"], a["t"]))
    a = {k: v[o] for k, v in a.items()}
    keep = (a["t"] >= t0) & (a["t"] < t1) & (a["bid"] > 0) & (a["ask"] >= a["bid"])
    a = {k: v[keep] for k, v in a.items()}
    k2 = np.r_[True, np.diff(a["t"]) > 0]       # одно состояние от двух линий — одна запись на T
    return {k: v[k2] for k, v in a.items()}


def lhist(err_pct):
    """Гистограмма log10 ошибки (в %): индексы 0…LB−1; нулевые (< EPS %) отдельно."""
    z = err_pct < EPS * 100
    v = err_pct[~z]
    i = np.clip(np.floor((np.log10(v) - LOG_LO) / LOG_STEP).astype(np.int64), 0, LB - 1)
    return int(z.sum()), np.bincount(i, minlength=LB).astype(np.int64)


def consistency(S, B):
    """Для каждой пачки: цена первой сделки против того же края в снимке до и после неё. Пачка внутри [min, max] двух снимков — согласована."""
    st = S["t"]
    k = np.searchsorted(st, B["t"], "right")
    ok = (k >= 1) & (k < len(st))
    k, buy, p = k[ok], (B["side"] > 0)[ok], B["first"][ok]
    q_prev = np.where(buy, S["ask"][k - 1], S["bid"][k - 1])
    q_next = np.where(buy, S["ask"][k], S["bid"][k])
    lo, hi = np.minimum(q_prev, q_next), np.maximum(q_prev, q_next)
    below, above = p < lo * (1 - EPS), p > hi * (1 + EPS)
    err = np.where(below, (lo - p) / p, np.where(above, (p - hi) / p, 0.0)) * 100
    res = {}
    for name, m in (("all", np.ones(len(p), bool)), ("buy", buy), ("sell", ~buy), ("single", B["n"][ok] == 1), ("sweep", B["n"][ok] > 1)):
        n = int(m.sum())
        z, h = lhist(err[m & (below | above)]) if (m & (below | above)).any() else (0, np.zeros(LB, np.int64))
        res[name] = {"n": n, "inside": int((m & ~below & ~above).sum()), "eq_prev": int((m & (np.abs(p / q_prev - 1) < EPS)).sum()),
                     "eq_next": int((m & (np.abs(p / q_next - 1) < EPS)).sum()), "below": int((m & below).sum()), "above": int((m & above).sum()), "hist": h}
    # касание: пачки, у которых верх снимка не менялся между снимками (bid и ask до = после): цена против bid / ask снимка (5 классов), по сторонам
    stable = (S["bid"][k - 1] == S["bid"][k]) & (S["ask"][k - 1] == S["ask"][k])
    bid, ask = S["bid"][k - 1], S["ask"][k - 1]
    rel = lambda a, b: np.abs(a / b - 1) < EPS  # noqa: E731
    cl = np.select([p < bid * (1 - EPS), rel(p, bid), p < ask * (1 - EPS), rel(p, ask)], [0, 1, 2, 3], 4)     # 0 ниже bid, 1 = bid, 2 внутри спреда, 3 = ask, 4 выше ask
    res["touch"] = {nm: {"n": int((stable & m).sum()), "cls": np.bincount(cl[stable & m], minlength=5).astype(np.int64),
                         "imp_hist": np.histogram(np.where(buy, (ask - p) / p, (p - bid) / p)[stable & m & (cl == 2)] * 100, TOUCH_EDGES)[0].astype(np.int64)}
                    for nm, m in (("buy", buy), ("sell", ~buy))}
    res["touch"]["spread_pct"] = float(np.median(((ask - bid) / ((ask + bid) / 2))[stable])) * 100 if stable.any() else None
    res["dt_prev_ms"] = np.percentile(B["t"][ok] - st[k - 1], (50, 90)).tolist() if ok.any() else None
    res["dt_next_ms"] = np.percentile(st[k] - B["t"][ok], (50, 90)).tolist() if ok.any() else None
    return res


def predict(S, B):
    """Оценка ask/bid на момент снимка k по снимку k−1 и пачкам в (T_{k−1}, T_k] против снимка k; база — снимок k−1 без сделок.
    Покупка по p ⇒ ask = p (последняя цена пачки — оценка ask после неё); продажа ⇒ bid = p. Если оценки пересеклись — побеждает более поздняя."""
    st = S["t"]
    kk = np.searchsorted(st, B["t"], "left")
    ok = (kk >= 1) & (kk < len(st))
    kk, tb, side, last = kk[ok], B["t"][ok], B["side"][ok], B["last"][ok]

    def last_per(sel):
        idx = np.flatnonzero(sel)
        u, fi = np.unique(kk[idx][::-1], return_index=True)
        j = idx[::-1][fi]
        return u, last[j], tb[j]

    ub, pb, tbb = last_per(side > 0)
    us, ps, tss = last_per(side < 0)
    ids = np.union1d(ub, us)
    n_int = len(st) - 1
    ask_p, bid_p = S["ask"][ids - 1], S["bid"][ids - 1]
    ask_t, bid_t = S["ask"][ids], S["bid"][ids]
    ask_e, bid_e = ask_p.copy(), bid_p.copy()
    ib, isl = np.searchsorted(ids, ub), np.searchsorted(ids, us)
    has_b, has_s = np.zeros(len(ids), bool), np.zeros(len(ids), bool)
    has_b[ib], has_s[isl] = True, True
    ask_e[ib] = pb
    bid_e[isl] = ps
    t_b, t_s = np.zeros(len(ids), np.int64), np.zeros(len(ids), np.int64)
    t_b[ib], t_s[isl] = tbb, tss
    cross = ask_e < bid_e
    only_b, only_s = has_b & ~has_s, has_s & ~has_b
    bid_e = np.where(only_b | (cross & (t_b >= t_s)), np.minimum(bid_e, ask_e), bid_e)
    ask_e = np.where(only_s | (cross & (t_s > t_b)), np.maximum(ask_e, bid_e), ask_e)
    res = {"n_int": int(n_int), "n_trade_int": int(len(ids))}
    for name, e, b, t, m in (("ask", ask_e, ask_p, ask_t, has_b), ("bid", bid_e, bid_p, bid_t, has_s), ("mid", (ask_e + bid_e) / 2, (ask_p + bid_p) / 2, (ask_t + bid_t) / 2, np.ones(len(ids), bool))):
        err_e, err_b = np.abs(e / t - 1) * 100, np.abs(b / t - 1) * 100
        ch = err_b > EPS * 100          # снимок изменился относительно k−1
        d = {}
        for sub, mm in (("all", m), ("changed", m & ch)):
            ze, he = lhist(err_e[mm]) if mm.any() else (0, np.zeros(LB, np.int64))
            zb, hb = lhist(err_b[mm]) if mm.any() else (0, np.zeros(LB, np.int64))
            d[sub] = {"n": int(mm.sum()), "exact_est": ze, "exact_base": zb, "hist_est": he, "hist_base": hb,
                      "better": int((mm & (err_e < err_b - EPS * 100)).sum()), "worse": int((mm & (err_e > err_b + EPS * 100)).sum())}
        res[name] = d
    return res


def check(coins, snaps, twork, t0, t1, out):
    res = {}
    for sym, info in coins.items():
        S = load_snaps(snaps, sym, t0, t1)
        mult = info.get("mult", 1.0)
        tr = bx.load_trades(twork, sym, mult, t0, t1)
        if S is None or tr is None or len(S["t"]) < 50:
            continue
        S["bid"], S["ask"] = S["bid"] / mult, S["ask"] / mult
        B = bx.make_batches(tr)
        sp = (S["ask"] - S["bid"]) / ((S["ask"] + S["bid"]) / 2) * 100
        res[sym] = {"coin": info.get("coin"), "n_snap": int(len(S["t"])), "snap_gap_ms": float(np.median(np.diff(S["t"]))), "spread_med_pct": float(np.median(sp)),
                    "n_batches": int(len(B["t"])), "cons": consistency(S, B), "pred": predict(S, B)}
    with open(out, "wb") as f:
        pickle.dump({"t0": t0, "t1": t1, "res": res}, f, protocol=4)
    print({"coins": len(res)})


if __name__ == "__main__":
    if sys.argv[1] == "parse":
        parse(json.load(open(sys.argv[2])), sys.argv[3], int(sys.argv[4]), int(sys.argv[5]) if len(sys.argv) > 5 else 4)
    else:
        check(json.load(open(sys.argv[2])), sys.argv[3], sys.argv[4], int(sys.argv[5]), int(sys.argv[6]), sys.argv[7])
