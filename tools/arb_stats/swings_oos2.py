"""Прогноз качелей вне выборки по преддекларации (docs/findings/arb-swings-prereg-2026-11-15.md): разработка / тест, плацебо.

Запуск (srv, юнитом):
  python3 swings_oos2.py dev    <swings2.pkl> <oos2_dev.pkl> [PAR]                         — гиперпараметры и разведка только по разработке (тест не читается)
  python3 swings_oos2.py selftest <swings2.pkl> <oos2_dev.pkl> [PAR]                       — время и здравый смысл плацебо на РАЗРАБОТКЕ (R = 2)
  python3 swings_oos2.py final  <swings2.pkl> <oos2_dev.pkl> <oos2_final.pkl> <sha_преддекларации> [PAR] [R_SHUF] [R_PHASE]
final печатает/считает тестовые сутки; отказывается, если файл скрипта не тот, что считал разработку (sha256 в oos2_dev.pkl).

Время — смещения от t0 (мс). Разработка: [t0, t_split), t_split = t1 − 24 ч; тест: [t_split, t1].
Точки отсчёта: разработка — t0 + 8 ч + k·30 мин с t + 1 ч ≤ t_split; тест — t_split + k·30 мин с t + 1 ч ≤ t1. Исход H ч: число качелей (закрытие) в (t, t + H], NaN, если t + H за концом выборки.
Предикторы (качелей/ч, только данные ≤ t): naive24 (последние 24 ч, базовая линия «круги вчера»), glob, win6, reg (BOCPD по интервалам, λ заморожена по разработке;
режим берётся, если возраст ≥ задержка обнаружения + 3 интервала и ≥ задержки в часах; иначе glob).
Популяция клетки в точке t — допустимые пары группы с ≥ 1 качелей до t; Спирмен считается по ней, клетка считается, если пар ≥ 30.
"""
import hashlib
import json
import pickle
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from scipy.stats import rankdata

sys.path.insert(0, str(Path(__file__).parent))
import bocpd  # noqa: E402
import swings  # noqa: E402

HOUR = swings.HOUR
NS = swings.NS
LS = (300, 10000)
HS = (1, 6, 12, 24)
PREDS = ("glob", "win6", "reg")
ALLP = ("naive24",) + PREDS
A0, B0 = 0.1, 1.0                      # сглаживание частоты: (A0 + k) / (B0 + часы)
STEP = 30 * 60000
START_H = 8
MIN_POP = 30
FLAG_RATE = 1.0                         # флаг «≥ 1 качели/ч»
FEE_TT = 0.126                          # %: 4 тейкера (допустимо, если 2N > комиссии круга)
LAMS_SW = (5, 15, 50, 150, 10 ** 9)     # интервалов между сменами режима потока
SEED = 20261115
G = {}


def script_sha():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


# ------------------------------------------------------------------ подготовка
def prep(d):
    keys = sorted(d["pairs"])
    t0, t1 = d["t0"], d["t1"]
    Ts, Te = (t1 - 24 * HOUR) - t0, t1 - t0
    P = len(keys)
    grp = np.array([1 if "bingx" in k[1:] else 0 for k in keys], np.int8)          # 1 = bx
    tick = np.array([max(d["info"][k[0]][k[1]]["tick"], d["info"][k[0]][k[2]]["tick"]) for k in keys])
    nmin = int(Ts // 60000)
    Gp = np.array([2.0 * float(np.nanmedian(d["pairs"][k]["hm"][:nmin])) for k in keys])
    adm = (np.asarray(NS)[None, :] >= np.maximum(3 * tick, Gp)[:, None]) & (2 * np.asarray(NS)[None, :] > FEE_TT)
    start = np.array([max(d["pairs"][k]["t0e"], 0) for k in keys], np.int64)
    ev = {}
    for p, k in enumerate(keys):
        for iN in range(len(NS)):
            for iL, L in enumerate(LS):
                ev[(p, iN, iL)] = np.sort(d["pairs"][k]["sw"][(iN, L)][0].astype(np.int64))
    G.update({"keys": keys, "P": P, "grp": grp, "tick": tick, "G": Gp, "adm": adm, "start": start, "ev": ev, "t0": t0, "t1": t1, "Ts": Ts, "Te": Te})


def origins_dev():
    n = int((G["Ts"] - HOUR - START_H * HOUR) // STEP) + 1
    return START_H * HOUR + np.arange(n, dtype=np.int64) * STEP


def origins_test():
    n = int((G["Te"] - HOUR - G["Ts"]) // STEP) + 1
    return G["Ts"] + np.arange(n, dtype=np.int64) * STEP


# ------------------------------------------------------------------ ряд одной пары·N·L
def series_preds(tc, start, to, end, lam, dly):
    """tc — отсортированные закрытия качелей (мс от t0), to — точки отсчёта, end — конец выборки (исход NaN за концом).
    -> pred (4, no) в порядке ALLP, y (len(HS), no), hist (no)."""
    k = np.searchsorted(tc, to, "right")
    expo = np.maximum((to - start) / HOUR, 0.0)
    glob = (A0 + k) / (B0 + expo)
    k6 = k - np.searchsorted(tc, to - 6 * HOUR, "right")
    win6 = (A0 + k6) / (B0 + np.minimum(6.0, expo))
    k24 = k - np.searchsorted(tc, to - 24 * HOUR, "right")
    naive = (A0 + k24) / (B0 + np.minimum(24.0, expo))
    A = np.r_[start, tc]
    tau = np.maximum(np.diff(A) / 1000.0, 1e-3)
    rl = bocpd.bocpd_exp(tau, 1.0 / lam)[0]
    r = np.where(k >= 1, rl[np.maximum(k - 1, 0)], 0)
    ts_ = A[np.maximum(k - r, 0)]
    age_h = (to - ts_) / HOUR
    use = (r >= dly[0] + 3) & (age_h >= dly[1])
    reg = np.where(use, (A0 + r) / (B0 + age_h), glob)
    y = np.full((len(HS), len(to)), np.nan, np.float32)
    for ih, h in enumerate(HS):
        e = to + h * HOUR
        y[ih] = np.where(e <= end, np.searchsorted(tc, e, "right") - k, np.nan)
    return np.stack([naive, glob, win6, reg]).astype(np.float32), y, k.astype(np.int32)


def build(evfun, orig, end, lam, dly):
    """Плотные массивы по всем допустимым парам: pred (nL, nN, 4, P, no), y (nL, nN, nH, P, no), hist (nL, nN, P, no)."""
    P, no, nN, nL = G["P"], len(orig), len(NS), len(LS)
    pred = np.full((nL, nN, 4, P, no), np.nan, np.float32)
    y = np.full((nL, nN, len(HS), P, no), np.nan, np.float32)
    hist = np.zeros((nL, nN, P, no), np.int32)
    for p in range(P):
        for iN in range(nN):
            if not G["adm"][p, iN]:
                continue
            for iL in range(nL):
                tc = evfun(p, iN, iL)
                if tc is None or len(tc) == 0:
                    continue
                pr, yy, k = series_preds(tc, G["start"][p], orig, end, lam, dly.get((int(G["grp"][p]), iN, iL), (0.0, 0.0)))
                pred[iL, iN, :, p], y[iL, iN, :, p], hist[iL, iN, p] = pr, yy, k
    return {"pred": pred, "y": y, "hist": hist}


def real_events(end):
    return lambda p, iN, iL: (lambda a: a[a <= end])(G["ev"][(p, iN, iL)])


# ------------------------------------------------------------------ плацебо (фаз-рандомизация AAFT по часовым счётчикам)
def aaft_counts(c, rng):
    """AAFT (amplitude-adjusted FT): сохраняет множество значений счётчика и (приближённо) спектр, ломает фазы."""
    n = len(c)
    order = np.argsort(c, kind="stable")
    srt = c[order]
    rank = np.empty(n, np.int64)
    rank[order] = np.arange(n)
    g = np.sort(rng.standard_normal(n))[rank]
    F = np.fft.rfft(g)
    ph = rng.uniform(0, 2 * np.pi, len(F))
    ph[0] = 0.0
    if n % 2 == 0:
        ph[-1] = 0.0
    gp = np.fft.irfft(np.abs(F) * np.exp(1j * ph), n)
    return srt[np.argsort(np.argsort(gp, kind="stable"), kind="stable")]


def surrogate_events(tc, nb, rng):
    """Часовые счётчики первых nb часов → AAFT → события равномерно внутри часа; события после nb часов остаются как есть."""
    inside = tc[tc < nb * HOUR]
    c = np.bincount(inside // HOUR, minlength=nb)[:nb]
    c2 = aaft_counts(c, rng)
    ts = np.repeat(np.arange(nb, dtype=np.int64) * HOUR, c2) + rng.integers(0, HOUR, int(c2.sum()))
    return np.sort(np.r_[ts, tc[tc >= nb * HOUR]])


# ------------------------------------------------------------------ счёт клеток
def cells():
    return [(g, iN, iL, ih, ip) for g in (0, 1) for iN in range(len(NS)) for iL in range(len(LS)) for ih in range(len(HS)) for ip in range(len(ALLP))]


def pop_mask(B, g, iN, iL):
    return (G["grp"] == g)[:, None] & G["adm"][:, iN][:, None] & (B["hist"][iL, iN] >= 1)


def rho_mean(x, z, m, rng=None):
    """Спирмен (по популяции) в каждой точке, среднее по точкам, где пар ≥ MIN_POP. rng — перетасовка предиктора между парами. -> (среднее, точек, пар в первой точке)."""
    rs, first = [], 0
    for o in range(x.shape[1]):
        mm = m[:, o] & np.isfinite(x[:, o]) & np.isfinite(z[:, o])
        n = int(mm.sum())
        if o == 0:
            first = n
        if n < MIN_POP:
            continue
        a, b = rankdata(x[mm, o]), rankdata(z[mm, o])
        if rng is not None:
            a = a[rng.permutation(n)]
        if a.min() == a.max() or b.min() == b.max():
            continue
        rs.append(np.corrcoef(a, b)[0, 1])
    return (float(np.mean(rs)) if rs else np.nan), len(rs), first


def score_cells(B, orig_idx=None, rng=None):
    """-> {клетка: (Спирмен, точек, пар в 1-й точке)} по всем клеткам; rng ≠ None — плацебо «пары»."""
    out = {}
    for g in (0, 1):
        for iN in range(len(NS)):
            for iL in range(len(LS)):
                pop = pop_mask(B, g, iN, iL)
                for ih in range(len(HS)):
                    yy = B["y"][iL, iN, ih]
                    for ip in range(len(ALLP)):
                        out[(g, iN, iL, ih, ip)] = rho_mean(B["pred"][iL, iN, ip], yy, pop, rng)
    return out


def flag_cells(B, step=2):
    """Флаг «≥ 1 качели/ч» на часовых точках (каждая 2-я точка 30 мин), горизонт 1 ч. -> {(g, iN, iL, ip): (флагов, точность, базовая доля, подъём)}."""
    out = {}
    sl = slice(0, None, step)
    for g in (0, 1):
        for iN in range(len(NS)):
            for iL in range(len(LS)):
                pop = pop_mask(B, g, iN, iL)[:, sl]
                yy = B["y"][iL, iN, 0][:, sl]
                fin = pop & np.isfinite(yy)
                base = float((yy[fin] >= 1).mean()) if fin.any() else np.nan
                for ip in range(len(ALLP)):
                    pr = B["pred"][iL, iN, ip][:, sl]
                    fl = fin & np.isfinite(pr) & (pr >= FLAG_RATE)
                    n = int(fl.sum())
                    prec = float((yy[fl] >= 1).mean()) if n else np.nan
                    out[(g, iN, iL, ip)] = (n, prec, base, prec / base if n and base > 0 else np.nan)
    return out


# ------------------------------------------------------------------ гиперпараметры по разработке
def ev_job(a):
    p, iN, iL = a
    tc = G["ev"][(p, iN, iL)]
    tc = tc[tc <= G["Ts"]]
    tau = np.maximum(np.diff(np.r_[G["start"][p], tc]) / 1000.0, 1e-3)
    return {lam: bocpd.bocpd_exp(tau, 1.0 / lam)[1] for lam in LAMS_SW}


def delay_job(a):
    p, iN, iL = a
    tc = G["ev"][(p, iN, iL)]
    tc = tc[tc <= G["Ts"]]
    A = np.r_[G["start"][p], tc]
    tau = np.maximum(np.diff(A) / 1000.0, 1e-3)
    rl = bocpd.bocpd_exp(tau, 1.0 / G["lam"])[0].astype(np.int64)
    drop = np.flatnonzero(rl[1:] < rl[:-1] - 2) + 1          # t — индекс интервала; режим занимает интервалы t − r + 1 … t
    r = rl[drop]
    return r, (A[drop + 1] - A[drop + 1 - r]) / HOUR


def dev_hyper(par):
    rng = np.random.default_rng(5)
    cand = [(p, iN, iL) for p in range(G["P"]) for iN in range(len(NS)) for iL in range(len(LS))
            if G["adm"][p, iN] and (G["ev"][(p, iN, iL)] <= G["Ts"]).sum() >= 40]
    pick = [cand[i] for i in rng.choice(len(cand), min(200, len(cand)), replace=False)]
    with Pool(par) as pool:
        ev = pool.map(ev_job, pick, chunksize=4)
    tot = {lam: sum(e[lam] for e in ev) for lam in LAMS_SW}
    G["lam"] = max(tot, key=tot.get)
    cand = [(p, iN, iL) for p in range(G["P"]) for iN in range(len(NS)) for iL in range(len(LS))
            if G["adm"][p, iN] and (G["ev"][(p, iN, iL)] <= G["Ts"]).sum() >= 30]
    with Pool(par) as pool:
        res = pool.map(delay_job, cand, chunksize=4)
    pooled = {}
    for (p, iN, iL), (r, h) in zip(cand, res):
        c = pooled.setdefault((int(G["grp"][p]), iN, iL), [[], [], 0])
        c[0] += list(r)
        c[1] += list(h)
        c[2] += 1
    table = {}
    for k, (r, h, n) in pooled.items():
        q = lambda a: [float(x) for x in np.percentile(a, [25, 50, 75])] if len(a) else [np.nan] * 3  # noqa: E731
        table[k] = {"series": n, "drops": len(r), "d_int": q(r), "d_h": q(h)}
    dly = {}
    for g in (0, 1):
        for iL in range(len(LS)):
            gr = [x for (gg, _, ll), c in pooled.items() if gg == g and ll == iL for x in c[0]]
            gh = [x for (gg, _, ll), c in pooled.items() if gg == g and ll == iL for x in c[1]]
            ar = [x for (_, _, ll), c in pooled.items() if ll == iL for x in c[0]]
            ah = [x for (_, _, ll), c in pooled.items() if ll == iL for x in c[1]]
            for iN in range(len(NS)):
                t = table.get((g, iN, iL))
                if t and t["drops"] >= 10:                 # своя клетка: ≥ 10 падений
                    dly[(g, iN, iL)] = (t["d_int"][1], t["d_h"][1])
                elif len(gr) >= 10:                        # иначе группа·L по всем N
                    dly[(g, iN, iL)] = (float(np.median(gr)), float(np.median(gh)))
                elif len(ar) >= 10:                        # иначе обе группы по L
                    dly[(g, iN, iL)] = (float(np.median(ar)), float(np.median(ah)))
                else:
                    dly[(g, iN, iL)] = (0.0, 0.0)
    return tot, table, dly


# ------------------------------------------------------------------ плацебо: задача одной реплики
def plac_job(a):
    kind, r = a
    rng = np.random.default_rng([SEED, r, 0 if kind == "shuf" else 1])
    if kind == "shuf":
        return kind, r, score_cells(G["real"], rng=rng), None
    nb = int(G["end"] // HOUR)
    B = build(lambda p, iN, iL: surrogate_events(G["ev"][(p, iN, iL)][G["ev"][(p, iN, iL)] <= G["end"]], nb, rng) if (G["ev"][(p, iN, iL)] <= G["end"]).any() else None,
              G["orig"], G["end"], G["lam"], G["dly"])
    return kind, r, score_cells(B), flag_cells(B)


def run_placebo(par, rs, rp):
    jobs = [("phase", r) for r in range(rp)] + [("shuf", r) for r in range(rs)]
    shuf, phase, pflag = {}, {}, {}
    t = time.time()
    with Pool(par) as pool:
        for k, (kind, r, sc, fl) in enumerate(pool.imap_unordered(plac_job, jobs, chunksize=1)):
            if kind == "shuf":
                shuf[r] = sc
            else:
                phase[r], pflag[r] = sc, fl
            if k % 10 == 0:
                print(f"плацебо {k + 1}/{len(jobs)}, {time.time() - t:.0f} с", flush=True)
    return shuf, phase, pflag


def dump_result(dst, mode, sha, sc, fl, shuf, phase, pflag, R):
    pickle.dump({"mode": mode, "prereg_sha": sha, "script_sha256": script_sha(), "lam_sw": G["lam"], "dly": G["dly"], "orig": G["orig"], "keys": G["keys"], "grp": G["grp"], "adm": G["adm"], "G": G["G"],
                 "score": sc, "flags": fl, "real": G["real"], "shuf": shuf, "phase": phase, "pflag": pflag, "t0": G["t0"], "t1": G["t1"], "Ts": G["Ts"], "Te": G["Te"], "R": R}, open(dst, "wb"), protocol=4)


def main():
    mode, src = sys.argv[1], sys.argv[2]
    d = pickle.load(open(src, "rb"))
    prep(d)
    dev = pickle.load(open(sys.argv[3], "rb")) if mode in ("selftest", "final") else None
    par = int(sys.argv[{"dev": 4, "selftest": 4, "final": 6}[mode]]) if len(sys.argv) > {"dev": 4, "selftest": 4, "final": 6}[mode] else 6
    t = time.time()
    if mode == "dev":
        tot, table, dly = dev_hyper(par)
        print({"lam_sw": G["lam"], "evidence": {k: round(v) for k, v in tot.items()}, "s": round(time.time() - t)}, flush=True)
        orig = origins_dev()
        B = build(real_events(G["Ts"]), orig, G["Ts"], G["lam"], dly)
        sc, fl = score_cells(B), flag_cells(B)
        pickle.dump({"mode": "dev", "script_sha256": script_sha(), "lam_sw": G["lam"], "evidence": tot, "delay_table": table, "dly": dly, "keys": G["keys"], "grp": G["grp"], "tick": G["tick"],
                     "G": G["G"], "adm": G["adm"], "orig": orig, "Ts": G["Ts"], "Te": G["Te"], "score": sc, "flags": fl, "t0": G["t0"], "t1": G["t1"]}, open(sys.argv[3], "wb"), protocol=4)
        print({"dev_origins": len(orig), "admitted_pair_N": int(G["adm"].sum()), "s": round(time.time() - t)})
        return
    G["lam"], G["dly"] = dev["lam_sw"], dev["dly"]
    if mode == "selftest":
        G["orig"], G["end"] = origins_dev(), G["Ts"]
        G["real"] = build(real_events(G["end"]), G["orig"], G["end"], G["lam"], G["dly"])
        shuf, phase, pflag = run_placebo(par, 2, 2)
        dump_result(sys.argv[3] + ".selftest", "selftest", "selftest", score_cells(G["real"]), flag_cells(G["real"]), shuf, phase, pflag, (2, 2))
        print({"mode": "selftest(dev)", "s": round(time.time() - t)})
        return
    if dev["script_sha256"] != script_sha():
        sys.exit("скрипт изменён после разработки (sha256) — финал не запускается")
    dst, sha = sys.argv[4], sys.argv[5]
    rs = int(sys.argv[7]) if len(sys.argv) > 7 else 200
    rp = int(sys.argv[8]) if len(sys.argv) > 8 else 200
    G["orig"], G["end"] = origins_test(), G["Te"]
    G["real"] = build(real_events(G["end"]), G["orig"], G["end"], G["lam"], G["dly"])
    sc, fl = score_cells(G["real"]), flag_cells(G["real"])
    print({"final_origins": len(G["orig"]), "real_scored_s": round(time.time() - t)}, flush=True)
    shuf, phase, pflag = run_placebo(par, rs, rp)
    dump_result(dst, "final", sha, sc, fl, shuf, phase, pflag, (rs, rp))
    print({"done_s": round(time.time() - t)})


if __name__ == "__main__":
    main()
