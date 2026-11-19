"""Счёт «что платит при задержке Токио»: жизнь расхождения ≥ θ (мс) и круги при задержке L = 0/1/2/5/10/20/50 мс на обеих ногах.

Запуск: python3 tokyo_run.py <каталог книг (с parsed/)> <каталог сделок (с parsed/)> <t0_мс> <t1_мс> <выход.pkl> [PAR]
Переменные: DEAD — json пропусков сбора (+ обрыв Binance 15.11 18:12:18–18:14:20 UTC добавляется здесь), COINS — список монет (проба), THETAS — bps через запятую,
MODES — «life», «tt», «mk» (по умолчанию все три), SHIFTS — сдвиги времени E второй биржи пары, мс (проба на сдвиг часов; только tt), MAXTASKS.
Отсечка устаревания: ряд пары берётся только в моменты, когда у обеих ног последнее изменение лучшей цены не старше 60 с (core.pair_series подменён здесь); центр —
по минутным барам этого же ряда. Модель кругов — прежний код: outlier.simulate_tt («к центру», тейкер-тейкер), cross.simulate_cross («из − в +»: выход при Buy ≤ c − θ / Sell ≥ c + θ),
maker.simulate / cross.simulate_mk_x (мейкер-вход на M по цене из H ± θ, хедж на H тейкером; закрытие тейкером «к центру» / «из − в +»).
Задержка L — и реакция на вход/выход (тейкер-тейкер), и хедж после сделки на M; для мейкер-входа ещё период пересчёта котировки D: режим «L» — D = L (не меньше 1 мс), режим «300» — D = 300 мс (как прежний отчёт).
Строка сводки — outlier_run.pair_stats + occ (занятый номинал, $).
"""
import json
import os
import pickle
import sys
import time
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import core  # noqa: E402
import cross  # noqa: E402
import maker  # noqa: E402
import maker_run  # noqa: E402
import outlier  # noqa: E402
import outlier_run as orun  # noqa: E402
from arb_live import symmap_for  # noqa: E402
from common import EXCHANGES, load_pool, norm  # noqa: E402

CAP_MS = int(os.environ.get("CAP_MS", 60_000))      # CAP_MS в окружении — только для сверки с прежним отчётом (отсечка выключена огромным значением)
THETAS = (10.0, 15.0, 20.0, 30.0, 50.0, 75.0, 100.0)
LATS = (0, 1, 2, 5, 10, 20, 50)
LIFE_BPD, LIFE_N = 40, 320            # гистограмма жизни: log10(мс) × 40, 0 … 8 декад
G = {}

_core_pair_series = core.pair_series


def _pair_series(A, B, t1, stale_ms=None):
    """core.pair_series без отсечки устаревания по hold (stale_ms=None — прежнее поведение, на нём стоят отчёты Tokyo): отсечку ведёт CAP_MS по A["e"] ниже. Боевой core.py (60 с по hold) не меняется."""
    return _core_pair_series(A, B, t1, stale_ms=stale_ms)


def pair_series_cap(A, B, t1, stale_ms=None):
    """core.pair_series только в моменты, когда обе ноги не старше CAP_MS (с последнего изменения лучшей цены). stale_ms — как у core.pair_series (maker / outlier передают None)."""
    T, S, Bu, ia, ib = _pair_series(A, B, t1, stale_ms)
    ok = (T - A["e"][ia] <= CAP_MS) & (T - B["e"][ib] <= CAP_MS)
    return T[ok], S[ok], Bu[ok], ia[ok], ib[ok]


core.pair_series = pair_series_cap


def life_stats(A, B, t0, t1):
    """Жизнь расхождения ≥ θ (исполнимая цена на θ выгоднее центра, любая из двух сторон): от первого момента до первого момента без расхождения. -> dict или None."""
    T, S, Bu, ia, ib = _pair_series(A, B, t1)
    if len(T) < 50:
        return None
    valid = (T - A["e"][ia] <= CAP_MS) & (T - B["e"][ib] <= CAP_MS)
    if valid.sum() < 50:
        return None
    mins, _, sb, bb = core.minute_bars(T[valid], S[valid], Bu[valid])
    m0, cen = core.rolling_centre(mins, sb, bb)
    cT = cen[np.clip(T // 60000 - m0, 0, len(cen) - 1)]
    inw = T >= t0
    dt = np.diff(np.r_[T, t1]).astype(float)
    if dt[inw].sum() <= 0:
        return None
    ES, EB = S - cT, cT - Bu
    out = {"valid_share": float(dt[valid & inw].sum() / dt[inw].sum()), "th": {}}
    n = len(T)
    for th in G["thetas"]:
        durs, cens, share = [], 0, 0.0
        for E in (ES, EB):
            with np.errstate(invalid="ignore"):
                cond = valid & inw & (E >= th)
            if not cond.any():
                continue
            d = np.diff(np.r_[False, cond, False].astype(np.int8))
            st, en = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
            cen_ = en >= n
            dur = np.where(cen_, t1, T[np.minimum(en, n - 1)]) - T[st]
            durs.append(dur[~cen_].astype(float))
            cens += int(cen_.sum())
            share += float(dt[cond].sum())
        dur = np.concatenate(durs) if durs else np.zeros(0)
        if not len(dur):
            continue
        out["th"][th] = {"n": int(len(dur)), "cens": cens, "q": [float(np.percentile(dur, p)) for p in (10, 50, 90)],
                         "surv": [float((dur > L).mean()) for L in LATS], "share_t": share / float(dt[inw].sum()),
                         "hist": np.bincount(np.clip(np.floor(np.log10(np.maximum(dur, 1.0)) * LIFE_BPD).astype(np.int64), 0, LIFE_N - 1), minlength=LIFE_N).astype(np.int64)}
    return out


def retime(P0, D):
    """Мейкер-вход: пересчёт зависящего от периода котировки D (состояние пары на t − D) поверх prep_mk_x; отсечка устаревания на обеих ногах."""
    M, H = P0["M"], P0["H"]
    tq = P0["tr_e"] - D
    im, ih = np.searchsorted(M["e"], tq, "right") - 1, np.searchsorted(H["e"], tq, "right") - 1
    cq = P0["cen"][np.clip(tq // 60000 - P0["m0"], 0, len(P0["cen"]) - 1)]
    valid = (im >= 0) & (ih >= 0) & ~np.isnan(cq)
    im, ih = np.maximum(im, 0), np.maximum(ih, 0)
    valid &= (tq - M["e"][im] <= CAP_MS) & (tq - H["e"][ih] <= CAP_MS)
    Q = dict(P0)
    Q.update(D=D, valid=valid, bM=M["bid"][im], aM=M["ask"][im], bH=H["bid"][ih], aH=H["ask"][ih], cq=cq)
    return Q


def stats_x(arr, un, t0, t1):
    st = orun.pair_stats(arr, un, t0, t1)
    if st is None:
        return None
    st = st[0]
    hold_all = np.where(np.isnan(arr[:, 6]), t1 - arr[:, 0], arr[:, 6])
    st["occ"] = float((np.minimum(arr[:, 4], orun.SIZE_CAP) * hold_all).sum() / (t1 - t0))
    return st


def work_coin(coin):
    g = G
    books, trades, info = {}, {}, {}
    for ex, (sym, mult, _) in g["coins"][coin].items():
        unit = float((g["meta"][ex].get(sym) or {}).get("ctVal") or 1.0) if ex == "okx" else 1.0
        a = maker_run.load_book(g["bwork"], ex, sym, mult, unit)
        if a is None or len(a["e"]) < 20:
            continue
        te, tp = maker_run.load_trades(g["twork"], ex, sym, mult)
        books[ex] = a
        trades[ex] = (te if te is not None else np.zeros(0, np.int64), tp if tp is not None else np.zeros(0))
        info[ex] = float(np.median((a["bid"] + a["ask"]) / 2))
    rows, life = {}, {}
    exs = [e for e in EXCHANGES if e in books]
    t0, t1 = g["t0"], g["t1"]
    tm = time.time()

    def put(mode, key, cfg, arr, un):
        arr = np.asarray(arr, float).reshape(-1, cross.NC)
        arr = orun.drop_dead(arr, orun.dead_of(key[1], key[2]), key_lat(cfg), t1)
        if not len(arr):
            return
        st = stats_x(arr, un, t0, t1)
        if st is not None:
            rows[(mode, *key, cfg)] = st

    def key_lat(cfg):
        return cfg[1]

    for i, a_ in enumerate(exs):
        for b_ in exs[i + 1:]:
            if not 0.9 < info[a_] / info[b_] < 1.1:
                continue
            key = (coin, a_, b_)
            if "life" in g["modes"]:
                r = life_stats(books[a_], books[b_], t0, t1)
                if r is not None:
                    life[key] = r
            if "tt" in g["modes"]:
                for sh in g["shifts"]:
                    Bk = books[b_]
                    if sh:
                        Bk = dict(Bk)
                        Bk["e"] = Bk["e"] + sh
                    P = outlier.prep_tt(books[a_], Bk, t0, t1)
                    if P is None:
                        continue
                    tag = f"s{sh}" if g["shifts"] != (0,) else "-"
                    for th in g["thetas"]:
                        ent = outlier.entries_for(P, th)
                        for lat in LATS:
                            arr, un = outlier.simulate_tt(P, ent, lat, "full")
                            put("tt", key, (th, lat, "c-t", tag), cross.ext9(arr, lat), un)
                            arr, un = cross.simulate_cross(P, ent, lat, th)
                            put("tt", key, (th, lat, "x1-t", tag), arr, un)
    print(f"{coin} life+tt {time.time() - tm:.0f}s rows {len(rows)}", flush=True)
    if "mk" in g["modes"]:
        for m_ in exs:
            for h_ in exs:
                if m_ == h_ or not 0.9 < info[m_] / info[h_] < 1.1:
                    continue
                P0 = cross.prep_mk_x(books[m_], books[h_], trades[m_][0], trades[m_][1], t0, t1, 300)
                if P0 is None:
                    continue
                key = (coin, m_, h_)
                for dmode in ("L", "300"):
                    if dmode == "300":
                        Q = retime(P0, 300)
                        cands = {th: maker.candidates(Q, th) for th in g["thetas"]}
                    for lat in LATS:
                        if dmode == "L":
                            Q = retime(P0, max(lat, 1))      # D = L, но не меньше 1 мс: иначе после мгновенного выхода та же сделка открывает круг снова
                            cands = {th: maker.candidates(Q, th) for th in g["thetas"]}
                        for th in g["thetas"]:
                            cand = cands[th]
                            arr, un = maker.simulate(Q, cand, lat, "tt")
                            put("mk", key, (th, lat, "c-t", dmode), cross.ext9(arr, lat, True), un)
                            arr, un = cross.simulate_mk_x(Q, cand, lat, "tt", th, True)
                            put("mk", key, (th, lat, "x1-t", dmode), arr, un)
    print(f"{coin} done {time.time() - tm:.0f}s rows {len(rows)}", flush=True)
    return rows, life


def main(bwork, twork, t0, t1, out, par=5):
    day = time.strftime("%Y-%m-%d", time.gmtime(t1 / 1000))
    symmap, day = symmap_for(day)
    meta = {ex: load_pool(ex, day)[1] for ex in EXCHANGES}
    coins = {}
    for ex, m in symmap.items():
        for sym, (coin, mult) in m.items():
            cur = coins.setdefault(coin, {}).get(ex)
            exact = norm(ex, sym)[0] == coin
            if cur is None or (exact and not cur[2]):
                coins[coin][ex] = (sym, mult, exact)
    dead = json.load(open(os.environ["DEAD"])) if os.environ.get("DEAD") else {}
    s = int(datetime(2026, 11, 15, 18, 12, 18, tzinfo=timezone.utc).timestamp() * 1000)
    if not os.environ.get("NO_BINANCE_GAP"):
        dead.setdefault("binance", []).append([s, s + 122_000])         # обрыв Binance 15.11 18:12:18–18:14:20 UTC (проверка arb-cross-exit, §2)
    dead.get("binance", []).sort()
    thetas = tuple(float(x) for x in os.environ["THETAS"].split(",")) if os.environ.get("THETAS") else THETAS
    modes = os.environ.get("MODES", "life,tt,mk").split(",")
    shifts = tuple(int(x) for x in os.environ["SHIFTS"].split(",")) if os.environ.get("SHIFTS") else (0,)
    G.update({"bwork": str(Path(bwork, "parsed")), "twork": str(Path(twork, "parsed")), "t0": t0, "t1": t1, "coins": coins, "meta": meta,
              "thetas": thetas, "modes": modes, "shifts": shifts})
    orun.G.update({"dead": dead})
    only = os.environ.get("COINS")
    names = sorted(coins) if not only else only.split(",")
    t = time.time()
    rows, life = {}, {}
    with Pool(par, maxtasksperchild=int(os.environ.get("MAXTASKS", "4"))) as p:
        for k, (r, lf) in enumerate(p.imap_unordered(work_coin, names, chunksize=1)):
            rows.update(r)
            life.update(lf)
            if k % 20 == 0:
                print(k, len(names), len(rows), len(life), round(time.time() - t), flush=True)
    with open(out, "wb") as f:
        pickle.dump({"t0": t0, "t1": t1, "days": orun.day_edges(t0, t1), "dead": dead, "rows": rows, "life": life, "thetas": thetas, "lats": LATS, "cap_ms": CAP_MS,
                     "modes": modes, "shifts": shifts, "fee_tt4": cross.FEE_TT4, "fee_mk": maker.FEE_TT, "size_cap": orun.SIZE_CAP, "coins": len(names)}, f, protocol=4)
    import resource
    print({"coins": len(names), "rows": len(rows), "life": len(life), "s": round(time.time() - t, 1), "max_rss_child_mb": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss // 1024})


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5], int(sys.argv[6]) if len(sys.argv) > 6 else 5)
