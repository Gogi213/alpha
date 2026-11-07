#!/usr/bin/env python3
"""TK-493, П-38 r2: мир данных C2 - универсум пар, рынки и сессии (§3.2, §3.6), загрузка пары, стадия рядов s/b/sigma/c/r6 с кэшем (K1-K3), боковые панели оборота.

Корень данных: ~/xdl9 (боевой) либо каталог `--test-world` (синтетика; не может лежать внутри ~/xdl9). Чтение панелей/оборота/фандинга - только через conv_guard.
Кэш рядов: <out>/cache/L<L>/pairs/<pid>.npy (float32 [3, N]: b, sigma, r6), cells/<cell>.npy (float32 [2, N]: c, число валидных пар), meta/<pid>.json (тик, оценки Ролла,
счётчики), N = (last_day - 2465 + 1) * 1440. Значения причинны (зависят только от прошлого), поэтому кэш last_day = 2478 годится для Pi_train (префикс).
"""
import csv
import hashlib
import json
import math
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conv_guard as G      # noqa: E402
import conv_lib as L        # noqa: E402

MIN_DAY = 1440
CLASS_CODE = {"крипта": "crypto", "акции+ETF": "eq", "металлы+энергия": "metal", "индексы": "idx", "фиат": "fx"}
MAIN_CLASSES = ("crypto", "eq", "metal")
UNIVERSE_SHA_PREFIX = "8c977521"
OPEN_SEM = {"bybit": "chain", "bingx": "chain", "bitget": "chain", "kraken": "chain", "lighter": "chain",
            "binance": "first_trade", "okx": "first_trade", "aster": "first_trade", "gate": "first_trade"}
HK_HOLIDAYS = {2465}                    # 01.10.2026 (праздник); даты окна - только из календаря addendum (--calendar)
HK_SESS = ((90, 240), (300, 480))       # минуты суток UTC [01:30, 04:00) и [05:00, 08:00)


def safe(sym):
    return "".join(ch if ch.isalnum() or ch in "-_.@" else "_" for ch in sym)


class World:
    def __init__(self, root, test=False, calendar=None, turn_dir=None):
        self.root = root
        self.test = test
        self.turn_dir = turn_dir or os.path.join(root, "turn")
        self.calendar = self._read_calendar(calendar)

    @staticmethod
    def _read_calendar(path):
        cal = {"hk_holidays": set(HK_HOLIDAYS), "cme": None}
        if path and os.path.isfile(path):
            for r in csv.DictReader(open(path, encoding="utf-8")):
                if r["market"] == "HK" and r["kind"] == "holiday":
                    cal["hk_holidays"].add(int(r["a"]))
                if r["market"] == "CME" and r["kind"] == "closed":
                    cal.setdefault("cme_closed", []).append((int(r["a"]), int(r["b"])))
                    cal["cme"] = True
        return cal

    # ---- универсум
    def universe(self, check_sha=True):
        path = os.path.join(self.root, "asset_pairs.csv")
        if check_sha and not self.test:
            h = G.sha256_file(path)
            raw = open(path, "rb").read()
            if not (h.startswith(UNIVERSE_SHA_PREFIX) or hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest().startswith(UNIVERSE_SHA_PREFIX)):
                raise SystemExit("asset_pairs.csv: sha256 %s не совпадает с 8c977521… (П-37 params table_asset_pairs)" % h)
        out = []
        with open(path, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                if r.get("c1d2") != "1" or r.get("scope") != "между биржами":
                    continue
                cls = CLASS_CODE.get(r["big1"])
                if cls is None or r["big1"] != r["big2"]:
                    continue
                a, b = (r["ex1"], r["sym1"]), (r["ex2"], r["sym2"])
                if (a[0], a[1]) > (b[0], b[1]):
                    a, b = b, a
                if a[0] == b[0]:
                    a, b = ((r["ex1"], r["sym1"]), (r["ex2"], r["sym2"])) if r["sym1"] < r["sym2"] else ((r["ex2"], r["sym2"]), (r["ex1"], r["sym1"]))
                pid = "%s~%s|%s|%s" % (a[0], b[0], safe(a[1]), safe(b[1]))
                out.append(dict(pid=pid, key=r["key"], cls=cls, exA=a[0], symA=a[1], exB=b[0], symB=b[1], cell="%s~%s/%s" % (a[0], b[0], cls),
                                quoteA=r.get("quote1", ""), quoteB=r.get("quote2", ""), legs=((a[0], a[1]), (b[0], b[1]))))
        out.sort(key=lambda p: p["pid"])
        for p in out:
            p["market"] = self.market_of(p)
        return out

    def market_of(self, p):
        if p["cls"] == "crypto":
            return "24x7"
        us = [os.path.isfile(os.path.join(self.root, "panels_us", e, safe_name(s) + ".sap")) for e, s in p["legs"]]
        hk = any(re.search(r"HKD", s) or re.match(r"^HK\d{4}", s) for _, s in p["legs"])
        if p["cls"] == "metal":
            return "CME" if self.calendar.get("cme") else "no_calendar"
        if all(us):
            return "US"
        if hk and not any(us):
            return "HK"
        return "other_tradfi"

    def in_main_series(self, p):
        return p["market"] in ("24x7", "US", "HK", "CME") and p["cls"] in MAIN_CLASSES

    def panel_path(self, p, side):
        ex, sym = p["legs"][side]
        sub = "panels_us" if p["market"] == "US" else "panels"
        return os.path.join(self.root, sub, ex, safe_name(sym) + ".sap")

    # ---- сессии
    def session_mask(self, market, m0, n):
        """Булев «в сессии» на минутах [m0, m0+n) либо None (24/7)."""
        if market in ("24x7",):
            return None
        mins = m0 + np.arange(n)
        day, tod = mins // MIN_DAY, mins % MIN_DAY
        dow = (day + 2) % 7
        wk = dow < 5
        if market == "US":
            o = np.where(day < 2497, 13 * 60 + 30, 14 * 60 + 30)
            return wk & (tod >= o) & (tod < o + 390)
        if market == "HK":
            hol = np.isin(day, sorted(self.calendar["hk_holidays"]))
            m = np.zeros(n, bool)
            for a, b in HK_SESS:
                m |= (tod >= a) & (tod < b)
            return m & wk & ~hol
        if market == "CME":
            m = np.ones(n, bool)
            for a, b in self.calendar.get("cme_closed", []):
                m &= ~((mins >= a) & (mins < b))
            return m
        return None

    def load_pair_raw(self, p, d0, d1):
        oA, cA = G.load_panel_range(self.panel_path(p, 0), d0, d1, what="pair " + p["pid"])
        oB, cB = G.load_panel_range(self.panel_path(p, 1), d0, d1, what="pair " + p["pid"])
        sess = self.session_mask(p["market"], d0 * MIN_DAY, len(cA))
        if sess is not None and p["market"] == "HK":
            for a in (oA, cA, oB, cB):
                a[~sess] = np.nan
        return oA, cA, oB, cB, sess

    def turn_path(self, ex, sym):
        return os.path.join(self.turn_dir, ex, safe_name(sym) + ".npy")

    def load_turn(self, p, side, d0, d1):
        ex, sym = p["legs"][side]
        fp = self.turn_path(ex, sym)
        if not os.path.isfile(fp):
            return None
        G.check_path(fp)
        G.check_days(d1, what="turn " + sym)
        a = np.load(fp, mmap_mode="r")
        n = (d1 - d0 + 1) * MIN_DAY
        assert len(a) >= n, "боковая панель оборота короче запроса"
        return np.asarray(a[:n], dtype=np.float64)

    def fund_array(self, p, side, d0, d1):
        ex, sym = p["legs"][side]
        fp = os.path.join(self.root, "funding", ex, safe_name(sym) + ".csv.gz")
        ts, rs = G.load_funding_range(fp, d0, d1)
        n = (d1 - d0 + 1) * MIN_DAY
        a = np.zeros(n)
        for m, r in zip(ts, rs):
            a[m - d0 * MIN_DAY] += r
        return a


def safe_name(sym):
    return "".join(c if c.isalnum() or c in "-_.@" else "_" for c in sym)


# ------------------------------------------------------------------ стадия рядов (K1-K3)
def cache_dirs(out, Lw):
    base = os.path.join(out, "cache", "L%d" % Lw)
    return os.path.join(base, "pairs"), os.path.join(base, "cells"), os.path.join(base, "meta")


def series_cell(world, cell, pairs, d0, d1, cfg, out):
    """Считает s, b, sigma, r6 каждой пары ячейки, общий фактор c ячейки, мета-статистики; пишет кэш. -> список мета пары."""
    pdir, cdir, mdir = cache_dirs(out, cfg.L)
    for d in (pdir, cdir, mdir):
        os.makedirs(d, exist_ok=True)
    N = (d1 - d0 + 1) * MIN_DAY
    Ds, metas, bs = [], [], []
    m0 = d0 * MIN_DAY
    for p in pairs:
        oA, cA, oB, cB, sess = world.load_pair_raw(p, d0, d1)
        s, valid = L.spread_series(cA, cB)
        tickA, tickB = L.tick_floor_bps(cA[:MIN_DAY]), L.tick_floor_bps(cB[:MIN_DAY])
        floor = max(tickA, tickB) if (math.isfinite(tickA) and math.isfinite(tickB)) else float("nan")
        b, sig, cnt = L.basis_sigma(s, cfg.L, cfg.step, cfg.nb, floor)
        r6 = L.short_ratio(s, b, sig, cfg.ls, cfg.nbs)
        d = s - b
        Ds.append(d)
        bs.append((b, sig, r6, cA, cB, valid, s, p, floor, tickA, tickB, sess))
    D = np.stack(Ds) if Ds else np.zeros((0, N))
    c, cntp = L.cell_common(D, 5)
    np.save(os.path.join(cdir, safe(cell) + ".npy"), np.stack([c, cntp.astype(np.float64)]).astype(np.float32))
    cf = c.astype(np.float32).astype(np.float64)
    for (b, sig, r6, cA, cB, valid, s, p, floor, tickA, tickB, sess) in bs:
        np.save(os.path.join(pdir, p["pid"] + ".npy"), np.stack([b, sig, r6]).astype(np.float32))
        dI = s - b - cf
        meta = pair_meta(p, cA, cB, valid, s, b, sig, dI, floor, tickA, tickB, sess, d0, cfg)
        with open(os.path.join(mdir, p["pid"] + ".json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, sort_keys=True)
        metas.append(meta)
    return metas


def pair_meta(p, cA, cB, valid, s, b, sig, dI, floor, tickA, tickB, sess, d0, cfg):
    """Мета пары: счётчики Q4, Ролл (обуч. 2465..2471 и полное), статистика регрессии полураспада по суткам, гистограммы серий пустых минут и доли протухших."""
    N = len(cA)
    out = dict(pid=p["pid"], n_valid=int(valid.sum()), floor=None if not math.isfinite(floor) else floor, tickA=_f(tickA), tickB=_f(tickB))
    ntr = min(N, (2472 - d0) * MIN_DAY)
    out["roll_full"] = [_f(L.roll_half_spread(cA)), _f(L.roll_half_spread(cB))]
    out["roll_train"] = [_f(L.roll_half_spread(cA[:ntr])), _f(L.roll_half_spread(cB[:ntr]))]
    with np.errstate(invalid="ignore"):
        out["mad0"] = float(np.mean((sig[np.isfinite(sig)] <= (floor if math.isfinite(floor) else 0) + 1e-12))) if np.isfinite(sig).any() else None
    # регрессия x_t на x_{t-15} по суткам t (сигнальные сутки >= 2466)
    nd = N // MIN_DAY
    st = np.zeros((nd, 6))
    x0, x1 = dI[:-15], dI[15:]
    ok = np.isfinite(x0) & np.isfinite(x1)
    dayidx = (np.arange(15, N) // MIN_DAY)
    for dd in range(nd):
        m = ok & (dayidx == dd)
        xa, ya = x0[m], x1[m]
        st[dd] = [m.sum(), xa.sum(), ya.sum(), (xa * xa).sum(), (xa * ya).sum(), (ya * ya).sum()]
    out["hl15_days"] = st.tolist()
    # автокорреляция d_idio по валидным наблюдениям, лаги 1..60 (обуч. и полное)
    for nm, lim in (("train", ntr), ("full", N)):
        v = dI[:lim][np.isfinite(dI[:lim])]
        ac = []
        if len(v) > 200:
            v = v - v.mean()
            den = float((v * v).sum())
            for lag in range(1, 61):
                ac.append(float((v[:-lag] * v[lag:]).sum() / den) if den > 0 else float("nan"))
        out["acf_" + nm] = ac
    # серии пустых минут и доля протухших (для K_stale, q_stale)
    for ci, (nm, vv) in enumerate((("A", np.isfinite(cA)), ("B", np.isfinite(cB)))):
        act = sess if sess is not None else None
        for tn, lim in (("train", ntr), ("full", N)):
            runs = L.nan_runs(vv[:lim], act[:lim] if act is not None else None)
            # длины завершённых серий: значения runs в минуте перед возвратом
            ends = np.flatnonzero((runs[:-1] > 0) & (runs[1:] == 0))
            lens = runs[ends]
            h = np.bincount(np.minimum(lens, 1440), minlength=1441)
            out["run_hist_%s_%s" % (nm, tn)] = h.astype(int).tolist()
            # доля пустых минут за предыдущие 60 (в сессии): гистограмма по округлённой k/60 (для q_stale)
            ac = act[:lim] if act is not None else np.ones(lim, bool)
            ecs = np.concatenate([[0], np.cumsum((~vv[:lim] & ac).astype(np.int64))])
            acs = np.concatenate([[0], np.cumsum(ac.astype(np.int64))])
            ix = np.arange(60, lim)
            den = (acs[ix] - acs[ix - 60]).astype(float)
            ok = den > 0
            fr = (ecs[ix] - ecs[ix - 60])[ok] / den[ok]
            out["stale_hist_%s_%s" % (nm, tn)] = np.bincount(np.round(fr * 60).astype(int), minlength=61).astype(int).tolist()
    return out


def _f(x):
    return None if x is None or not math.isfinite(x) else float(x)


def load_series(world, p, d0, d1, cfg, out, cell_c, want_turn=True, want_fund=True):
    """PairSeries пары из панелей + кэша рядов."""
    pdir, cdir, mdir = cache_dirs(out, cfg.L)
    N = (d1 - d0 + 1) * MIN_DAY
    oA, cA, oB, cB, sess = world.load_pair_raw(p, d0, d1)
    arr = np.load(os.path.join(pdir, p["pid"] + ".npy"), mmap_mode="r")
    assert arr.shape[1] >= N, "кэш рядов короче запроса"
    meta = json.load(open(os.path.join(mdir, p["pid"] + ".json"), encoding="utf-8"))
    b, sig, r6 = [np.asarray(arr[i, :N], dtype=np.float64) for i in range(3)]
    floor = meta["floor"] if meta["floor"] is not None else float("nan")
    tA = world.load_turn(p, 0, d0, d1) if want_turn else None
    tB = world.load_turn(p, 1, d0, d1) if want_turn else None
    frA = world.fund_array(p, 0, d0, d1) if want_fund else None
    frB = world.fund_array(p, 1, d0, d1) if want_fund else None
    admit = math.isfinite(floor)
    ps = L.PairSeries(oA, cA, oB, cB, d0 * MIN_DAY, cfg, key=(p["key"], p["pid"]), legs=p["legs"], exA=p["exA"], exB=p["exB"], cls=p["cls"], asset=p["key"],
                      b=b, sig=sig, r6=r6, c=cell_c[:N], floor=floor, admit=admit, turnA=tA, turnB=tB, sess=sess, frA=frA, frB=frB,
                      semA=OPEN_SEM.get(p["exA"], "first_trade"), semB=OPEN_SEM.get(p["exB"], "first_trade"))
    return ps, meta


def load_cell_c(out, cell, Lw):
    _, cdir, _ = cache_dirs(out, Lw)
    a = np.load(os.path.join(cdir, safe(cell) + ".npy"))
    return a[0].astype(np.float64), a[1]


def components(pairs):
    """Компоненты связности пар по общим ногам (союз-поиск). -> список списков пар, порядок детерминирован (по минимальному pid)."""
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for p in pairs:
        a, b = find(p["legs"][0]), find(p["legs"][1])
        if a != b:
            parent[max(a, b)] = min(a, b)
    comp = {}
    for p in pairs:
        comp.setdefault(find(p["legs"][0]), []).append(p)
    out = [sorted(v, key=lambda p: p["pid"]) for v in comp.values()]
    out.sort(key=lambda v: v[0]["pid"])
    return out


# ------------------------------------------------------------------ боковые панели оборота (G-C2-7)
def build_turnover(world, pairs, d0, d1, raw_root, outdir=None):
    """Строит <root>/turn/<ex>/<SYM>.npy float32 за [d0, d1] для ног пар: quote_v, где пуст - c*v (Gate: c*v*mult). Сырьё: <raw_root>/<биржа>/candles/<SYM>.csv.gz."""
    seen = set()
    n = 0
    for p in pairs:
        for ex, sym in p["legs"]:
            if (ex, sym) in seen:
                continue
            seen.add((ex, sym))
            src = os.path.join(raw_root, ex, "candles", safe_name(sym) + ".csv.gz")
            if not os.path.isfile(src):
                continue
            mult = 1.0
            a = G.load_turnover_raw(src, d0, d1, exchange=ex, mult=mult)
            dst = os.path.join(outdir or world.turn_dir, ex)
            os.makedirs(dst, exist_ok=True)
            np.save(os.path.join(dst, safe_name(sym) + ".npy"), a.astype(np.float32))
            n += 1
    return n


def verify_turnover(world, raw_root, d0, d1, pairs, n_sym=12, seed=37):
    """G-C2-7: боковые панели оборота против сырых свечей на n_sym символах: независимый разбор csv.gz (модуль csv), сравнение по всем минутам формирования.
    -> (проверено символов, максимум относительного расхождения, список расхождений)."""
    import gzip
    rng = np.random.default_rng(seed)
    legs = sorted({l for p in pairs for l in p["legs"]})
    pick = [legs[i] for i in rng.permutation(len(legs))[:n_sym]]
    bad = []
    mx = 0.0
    n_done = 0
    for ex, sym in pick:
        src = os.path.join(raw_root, ex, "candles", safe_name(sym) + ".csv.gz")
        fp = world.turn_path(ex, sym)
        if not (os.path.isfile(src) and os.path.isfile(fp)):
            continue
        G.check_days(d1, what="verify turnover")
        a = np.load(fp)
        m0, m1 = d0 * MIN_DAY, (d1 + 1) * MIN_DAY
        t_lo, t_hi = G.GRID0_MS + m0 * 60_000, G.GRID0_MS + m1 * 60_000
        ref = np.zeros(m1 - m0)
        with gzip.open(src, "rt", encoding="utf-8", newline="") as f:
            rd = csv.reader(f)
            next(rd, None)
            for r in rd:
                t = int(r[0])
                if t >= t_hi:
                    break
                if t < t_lo:
                    continue
                v = float(r[5])
                ref[(t - t_lo) // 60_000] = float(r[6]) if len(r) > 6 and r[6] != "" else float(r[4]) * v
        d = np.abs(a[:len(ref)].astype(np.float64) - ref) / np.maximum(np.abs(ref), 1.0)
        mx = max(mx, float(d.max()) if len(d) else 0.0)
        n_done += 1
        if len(d) and d.max() > 1e-6:
            bad.append((ex, sym, float(d.max())))
    return n_done, mx, bad
