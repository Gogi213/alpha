#!/usr/bin/env python3
"""П-42 §13.66 (TK-666): признаки входа F / P / C / R — дампы уровней, счёт без P&L (`counts`), клетки, G8, выбор. Определения — протокол §13.66 (PS-936), условия критика — §13.66.1.
  python3 tk666_run.py levels [окно]   — движок `breakout tick` с `P42_LEVELS=1`: `L_t240` / `S_t240` (база + `tf=240`) и контроль `L_t60` / `S_t60` (база) → /data/p42-tk666/levels/<окно>/<вариант>/<long|short>-levels.csv
  python3 tk666_run.py counts          — пороги, клетки, дубли, допуск, m_new, Jaccard, ворота G-src / G-rest / G-causal / G-lvl / G-cov, таблицы `--ftab` — БЕЗ P&L
  python3 tk666_run.py cells [окно]    — движок: клетки (после PASS критика: файл /data/p42-tk666/PASS)
  python3 tk666_run.py g8              — G8 / G-base / G-ref (читает счётчики и файлы сделок, после PASS)
  python3 tk666_run.py select          — Холм (g1), g2–g5, исход по п. 7 (читает P&L, после PASS)"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
import urllib.request

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import retitr_ftab as rfx  # noqa: E402
import tk604_run as r4  # noqa: E402
import tk604_stats as st  # noqa: E402
import tk625_run as r5  # noqa: E402
import tk625_stats as s5  # noqa: E402
import tk633_run as r33  # noqa: E402
import tk634_run as r34  # noqa: E402
import tk636_feat as f36  # noqa: E402
import tk638_post as p8  # noqa: E402
import tk666_feat as ft  # noqa: E402

ROOT = ft.ROOT
SIDES = st.SIDES
ENG, BASE = r5.ENG, r5.BASE
say, write_json = r4.say, r4.write_json
GuardError = st.GuardError
SIDE_NAME_RU = {"L": "лонг", "S": "шорт"}
SIGMA = {"L": -1.0, "S": 1.0}                       # п. 3: большое σ·x — благоприятно (топливо)
FV_PASS = 0.5                                       # `fa=<клетка> fo=lo fv=0.5`: исключает 0,0, пропускает 1,0
SHARES = ("0.10", "0.25", "0.40", "0.75")
N_OLD_TRIAL = 61                                    # 9 (TK-625) + 12 (TK-633) + 8 (TK-634) + 32 (TK-636), п. 6 (g1)
OLD_SELECT = ("/data/p42-tk625/res/select.json", "/data/p42-tk633/res/select33.json", "/data/p42-tk634/res/select34.json", "/data/p42-tk636/res/select36.json")
N_CTX_EXTRA = 53                                    # TK-648 (12) + TK-651 (17) + TK-656 (24): контекстный Холм (п. 6)
ALPHA = 0.05
SUBSET_SHARE, SUBSET_MIN_TOTAL, SUBSET_MIN_MONTH = 0.5, 70, 8   # п. 6: допуск подмножества
HYPS = (("F", "H1 фандинг", "F25"), ("P", "H2 премия", "P25"), ("C", "H4 слияние 60/240", "C02"), ("R", "H5 круглое число", "R05"))
N_GATE = (32, 93)                                   # шумовая планка: N независимых нулевых клеток (п. 6)
G_SEED = 666
V_CIDS = ("F75r", "P75r", "Cr", "Rr")
_C = {}


def q(*a):
    p = os.path.join(ROOT, *a)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def uni():
    return r5.Uni("h775")


def code_id():
    h = hashlib.sha256()
    for n in ("tk625_run.py", "tk625_stats.py", "tk634_run.py", "tk636_feat.py", "tk666_feat.py", "tk666_run.py"):
        h.update(open(os.path.join(HERE, n), "rb").read())
    return h.hexdigest()[:12]


def stamp(d):
    d["code_id"] = code_id()
    return d


# ---- levels ------------------------------------------------------------------------------------------------------------------------

def level_variants(u):
    """L_t240 / S_t240 — база стороны + `tf=240` (остальные ключи базы без изменений); L_t60 / S_t60 — контроль (база как есть, G-lvl)."""
    v = []
    for kind, extra in (("t240", " tf=240"), ("t60", "")):
        for s in SIDES:
            v.append(("%s_%s" % (s, kind), BASE[s] + u.extra + extra))
    return v


def cmd_levels(a):
    u = uni()
    names = [a.window] if a.window else [w.name for w in st.TRADE_WINDOWS]
    for name in names:
        w = r4.win(name)
        st.check_dates(w.t0, w.hi)
        out = q("levels", w.name)
        if os.path.exists(out + "/DONE"):
            continue
        r5.tick(u, w, level_variants(u), out, {"P42_LEVELS": "1"}, q("log", "levels-%s.log" % w.name))
        open(out + "/DONE", "w").close()
        say("levels %s готово" % w.name)


# ---- таблицы признаков ---------------------------------------------------------------------------------------------------------------

class Rows:
    """Строки таблицы признаков окна (`tk666_feat.py build`): ключ (монета, мс от T0)."""

    def __init__(self, wname, side):
        self.sym, self.rel, self.raw = rfx.read_ftab(q("feat", "%s-%s.csv" % (wname, side)))
        self.index = {}
        self.dup_keys = 0
        for i, (x, r) in enumerate(zip(self.sym, self.rel)):
            k = (x, int(r))
            if k in self.index:
                self.dup_keys += 1
            self.index[k] = i


def rows_of(wname, side):
    key = ("rows", wname, side)
    if key not in _C:
        _C[key] = Rows(wname, side)
    return _C[key]


def derive(raw, side):
    """Сырые столбцы + g_F = σ·f8, g_P = σ·prem (σ = −1 лонг, +1 шорт)."""
    d = {k: np.asarray(v, dtype=float) for k, v in raw.items()}
    d["gF"], d["gP"] = SIGMA[side] * d["f8"], SIGMA[side] * d["prem"]
    return d


def trade_cols(side):
    """Значения признаков на сделках базы стороны (P&L не читается): (окна, монеты, мс от T0, сутки, {столбец: массив}); охрана: у каждой сделки есть строка таблицы."""
    key = ("tc", side)
    if key in _C:
        return _C[key]
    u = uni()
    W, S, R = r34.trade_rows(u, side)
    days = []
    for w in st.TRADE_WINDOWS:
        t = st.read_trades_csv(os.path.join(u.base(w.name, side), ENG[side] + "-trades.csv"))
        d = st.abs_day(w, t.entry_ms)
        lo_i, hi_i = (w.lod - st.D0).days, (w.hid - st.D0).days
        days += [int(d[i]) for i in np.nonzero((d >= lo_i) & (d <= hi_i))[0]]
    day = np.array(days, dtype=np.int64)
    if len(day) != len(W):
        raise GuardError("%s: сутки сделок %d ≠ ключей %d" % (side, len(day), len(W)))
    raw = {}
    for w in st.TRADE_WINDOWS:
        rows = rows_of(w.name, side)
        sel = np.nonzero(W == w.name)[0]
        pos = np.array([rows.index.get((S[i], int(R[i])), -1) for i in sel], dtype=np.int64)
        if (pos < 0).any():
            raise GuardError("%s %s: %d сделок базы без строки таблицы признаков" % (side, w.name, int((pos < 0).sum())))
        for n, v in rows.raw.items():
            raw.setdefault(n, np.full(len(W), np.nan))[sel] = v[pos]
    out = (W, S, R, day, derive(raw, side))
    _C[key] = out
    return out


def thresholds(d):
    """Пороги g_F, g_P: квантили 0,10 / 0,25 / 0,40 / 0,75 по сделкам базы стороны (определённые значения, linear), одно число на все монеты (п. 4)."""
    thr = {}
    for f, col in (("F", "gF"), ("P", "gP")):
        v = d[col][np.isfinite(d[col])]
        thr[f] = {k: float(np.quantile(v, float(k), method="linear")) for k in SHARES}
    return thr


# ---- клетки --------------------------------------------------------------------------------------------------------------------------

def cell_specs():
    """Клетки стороны (п. 5): пробные T (F25, P25, C02, R05), соседи N (F10, F40, P10, P40, C01, C04, R025, R10), обратные V (F75r, P75r, Cr, Rr; описательно). Порядок обхода дублей: T → N → V."""
    T = [dict(cid="F25", kind="T", feat="F", share="0.25", group=1), dict(cid="P25", kind="T", feat="P", share="0.25", group=2),
         dict(cid="C02", kind="T", feat="C", tau="02", group=3), dict(cid="R05", kind="T", feat="R", delta=0.5, group=4)]
    N = [dict(cid="F10", kind="N", feat="F", share="0.10", group=1), dict(cid="F40", kind="N", feat="F", share="0.40", group=1),
         dict(cid="P10", kind="N", feat="P", share="0.10", group=2), dict(cid="P40", kind="N", feat="P", share="0.40", group=2),
         dict(cid="C01", kind="N", feat="C", tau="01", group=3), dict(cid="C04", kind="N", feat="C", tau="04", group=3),
         dict(cid="R025", kind="N", feat="R", delta=0.25, group=4), dict(cid="R10", kind="N", feat="R", delta=1.0, group=4)]
    V = [dict(cid="F75r", kind="V", feat="F", share="0.75", group=11), dict(cid="P75r", kind="V", feat="P", share="0.75", group=12),
         dict(cid="Cr", kind="V", feat="C", tau="02", group=13), dict(cid="Rr", kind="V", feat="R", delta=0.5, group=14)]
    return T + N + V


def excl_mask(sp, d, thr):
    """Исключённые строки (пусто проходит фильтр, п. 4). Обратные (V): F75r / P75r — g выше квантиля 0,75; Cr — слитые; Rr — ρ > 0,5 (оставляем уровни у круглого числа)."""
    f, v = sp["feat"], sp["kind"] == "V"
    if f in ("F", "P"):
        x = d["g" + f]
        t = thr[f][sp["share"]]
        return np.isfinite(x) & ((x > t) if v else (x < t))
    if f == "C":
        x = d["conf" + sp["tau"]]
        return np.isfinite(x) & ((x == 1) if v else (x == 0))
    if f == "R":
        x = d["rho"]
        return np.isfinite(x) & ((x > sp["delta"]) if v else (x <= sp["delta"]))
    raise GuardError("клетка %r неизвестна" % sp["cid"])


def write_cell_table(path, sym, rel, cols):
    names = list(cols)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("sym,sig_ms," + ",".join(names) + "\n")
        for i in range(len(sym)):
            f.write("%s,%d,%s\n" % (sym[i], int(rel[i]), ",".join("%.1f" % cols[c][i] for c in names)))
    return len(sym)


def admit_ex_ante(mk, mon):
    """Допуск до P&L (п. 6): исключено ≥ 30; клетка и база ≥ 20 в месяце (01–13.11 — 8); подмножество (остаётся < 50 %) — ≥ 70 сделок за окно и ≥ 8 в каждом месяце. (допущена, подмножество)"""
    n = len(mon)
    rem = [int(((mon == k) & ~mk).sum()) for k in range(len(st.MONTH_LEN))]
    base_m = [int((mon == k).sum()) for k in range(len(st.MONTH_LEN))]
    e = int(mk.sum())
    subset = (n - e) < SUBSET_SHARE * n
    if subset:
        ok = sum(rem) >= SUBSET_MIN_TOTAL and all(r >= SUBSET_MIN_MONTH for r in rem)
    else:
        ok = all(r >= st.MIN_TRADES[k] for k, r in enumerate(rem))
    return bool(ok and e >= st.MIN_EXCL and all(b >= st.MIN_TRADES[k] for k, b in enumerate(base_m))), subset


def mant_bucket(m):
    return np.where(~np.isfinite(m), -1, np.where(m < 2, 0, np.where(m < 5, 1, 2)))


MB_NAMES = ("1–2", "2–5", "5–10")


# ---- ворота (без P&L) --------------------------------------------------------------------------------------------------------------

def read_sigsx_signed(wname, side):
    """Цены уровней строк sigsx со знаком (проверка знака шорта, условие критика 1)."""
    import csv
    px = []
    with open(ft.sigsx_path(wname, side), encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            px.append(int(r["level_px_e8"]))
    return np.array(px, dtype=np.int64)


def gate_sign():
    """Условие 1: знак цены — по |цене| для R и C у обеих сторон; печатаем число отрицательных цен, ρ и конфлюэнс при −px = при px."""
    res = {}
    for side in SIDES:
        neg = tot = 0
        rho_bad = conf_bad = 0
        for w in st.TRADE_WINDOWS:
            r = ft.read_sigsx(w.name, side)
            px_signed = read_sigsx_signed(w.name, side)
            if not np.array_equal(px_signed, r["px"]):
                raise GuardError("знак: чтение sigsx разошлось")
            neg += int((px_signed < 0).sum())
            tot += len(px_signed)
            for i in range(0, len(px_signed), max(1, len(px_signed) // 400)):
                a, b = ft.round_feat(int(px_signed[i]), int(r["natr"][i])), ft.round_feat(-int(px_signed[i]), int(r["natr"][i]))
                if not (a[0] == b[0] or (a[0] != a[0] and b[0] != b[0])) or a[1] != b[1]:
                    rho_bad += 1
        res[side] = dict(rows=tot, negative_px=neg, rho_sign_mismatch=rho_bad)
    return res


def gate_src():
    """G-src: sha256 каждого использованного zip = `.sha256` рядом (из `.CHECKSUM` data.binance.vision)."""
    files = []
    for line in open(q("need-fund.tsv"), encoding="utf-8"):
        s_, mo = line.rstrip("\n").split("\t")
        p = ft.funding_zip_path(s_, mo)
        if p:
            files.append(p)
    for line in open(q("need-prem.tsv"), encoding="utf-8"):
        s_, d = line.rstrip("\n").split("\t")
        p = ft.prem_zip_path(s_, d)
        if p:
            files.append(p)
    bad, noc = [], 0
    for p in files:
        sp = p + ".sha256"
        if not os.path.exists(sp):
            noc += 1
            bad.append((p, "нет .sha256"))
            continue
        want = open(sp, encoding="utf-8").read().split()[0]
        got = hashlib.sha256(open(p, "rb").read()).hexdigest()
        if want != got:
            bad.append((p, "sha256 %s ≠ %s" % (got[:12], want[:12])))
    return dict(files=len(files), bad=bad, no_checksum=noc)


def gate_rest(n_coins=25):
    """G-rest: REST `fapi/v1/fundingRate` (25–31.10) = месячный архив 2026-10 построчно у ≥ 20 монет (первые по алфавиту с архивом октября и ноябрьскими кандидатами)."""
    start = int(__import__("datetime").datetime(2026, 10, 25, tzinfo=__import__("datetime").timezone.utc).timestamp() * 1000)
    end = ft.NOV_MS - 1
    syms = sorted(s_ for (s_,) in (tuple(l.rstrip("\n").split("\t")) for l in open(q("need-nov.tsv"), encoding="utf-8")) if ft.funding_zip_path(s_, "2026-10"))[:n_coins]
    res, bad = {}, []
    for s_ in syms:
        cp = q("gate-rest", s_ + ".json")
        if os.path.exists(cp):
            data = json.load(open(cp, encoding="utf-8"))
        else:
            url = "%s?symbol=%s&startTime=%d&endTime=%d&limit=1000" % (ft.REST, s_, start, end)
            with urllib.request.urlopen(url, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8"))
            json.dump(data, open(cp, "w", encoding="utf-8"))
            time.sleep(1.1)
        arch = [(t, rt) for t, _, rt in ft._read_funding_zip(ft.funding_zip_path(s_, "2026-10")) if start <= t <= end]
        rest = [(int(x["fundingTime"]), float(x["fundingRate"])) for x in data]
        ok = len(arch) == len(rest) and all(abs(a[0] - b[0]) <= 1000 and a[1] == b[1] for a, b in zip(sorted(arch), sorted(rest)))
        res[s_] = dict(archive=len(arch), rest=len(rest), equal=ok)
        if not ok:
            bad.append(s_)
    return dict(coins=len(syms), rows=sum(v["archive"] for v in res.values()), bad=bad, detail=res)


def gate_lvl():
    """G-lvl: дамп уровней движка при `tf=60` (контроль): у каждого кандидата базы есть живой на минуте `m` уровень с тем же рождением и той же ценой версии на `m` — 100 %, иначе стоп."""
    res, bad = {}, []
    for side in SIDES:
        for w in st.TRADE_WINDOWS:
            r = ft.read_sigsx(w.name, side)
            lv = ft.read_levels(ft.levels_path("t60", w.name, side))
            miss = 0
            for i in range(len(r["sym"])):
                m = int(r["sig"][i]) // 60_000
                hit = False
                for L in lv.get(r["sym"][i], ()):
                    if L[0] == int(r["born"][i]) and ft.level_px_at(L, m) == abs(int(r["px"][i])):
                        hit = True
                        break
                if not hit:
                    miss += 1
                    if len(bad) < 8:
                        bad.append("%s-%s %s m=%d px=%d" % (w.name, side, r["sym"][i], m, int(r["px"][i])))
            res["%s-%s" % (w.name, side)] = dict(rows=len(r["sym"]), miss=miss)
    return dict(res=res, bad=bad, ok=all(v["miss"] == 0 for v in res.values()))


def gate_causal(n=300):
    """G-causal: F и P — подмена данных строго после сигнала мусором не меняет g_F / g_P (≥ 300 строк, зерно 666); C — условие критика 3: без прогонов с усечённой лентой (ссылка на `born = now` и G-lvl 100 %); `conf_τ` берёт версию цены со временем ≤ m, подмена версий позже m мусором не меняет `conf`."""
    rng = np.random.default_rng(G_SEED)
    pool = []
    for side in SIDES:
        for w in st.TRADE_WINDOWS:
            r = ft.read_sigsx(w.name, side)
            for i in range(len(r["sym"])):
                pool.append((side, w, r, i))
    pick = rng.choice(len(pool), size=min(n, len(pool)), replace=False)
    fund_cache, prem, lv_cache = {}, ft.Prem(), {}
    bad_f = bad_p = bad_c = 0
    for j in pick:
        side, w, r, i = pool[int(j)]
        s_, ms = r["sym"][i], int(w.t0_ms + r["sig"][i])
        if s_ not in fund_cache:
            fund_cache[s_] = ft.load_funding(s_)
        t, rate8, inter = fund_cache[s_]
        f_clean = ft.funding_at(t, rate8, inter, ms)
        after = t > ms
        r2, i2 = rate8.copy(), inter.copy()
        r2[after], i2[after] = rng.normal(0.0, 0.05, int(after.sum())), 8.0
        f_dirty = ft.funding_at(t, r2, i2, ms)
        if not (f_clean[0] == f_dirty[0] or (f_clean[0] != f_clean[0] and f_dirty[0] != f_dirty[0])):
            bad_f += 1
        p_clean = prem.at(s_, ms)

        class Dirty(ft.Prem):
            def day(self, sym, day, _ms=ms, _rng=rng):
                d = ft.Prem.day(prem, sym, day)
                return None if d is None else {o: (float(_rng.normal(0.0, 0.05)) if o + 60_000 > _ms else v) for o, v in d.items()}

        p_dirty = Dirty().at(s_, ms)
        if not (p_clean[0] == p_dirty[0] or (p_clean[0] != p_clean[0] and p_dirty[0] != p_dirty[0])):
            bad_p += 1
        key = (w.name, side)
        if key not in lv_cache:
            pth = ft.levels_path("t240", w.name, side)
            lv_cache[key] = ft.read_levels(pth) if os.path.exists(pth) else None
        lv = lv_cache[key]
        if lv is not None:
            m = int(r["sig"][i]) // 60_000
            c_clean = ft.confluence(lv, s_, m, r["px"][i])
            dirty = {s_: [(b, e, mins, [(int(rng.integers(1, 10 ** 12)) if mm > m else p_) for mm, p_ in zip(mins, pxs)]) for b, e, mins, pxs in lv.get(s_, ())]}
            if c_clean != ft.confluence(dirty, s_, m, r["px"][i]):
                bad_c += 1
    return dict(rows=int(len(pick)), seed=G_SEED, bad_F=bad_f, bad_P=bad_p, bad_C=bad_c, ok=bool(bad_f == bad_p == bad_c == 0))


def cov_table(side):
    """G-cov: доля определённых значений F, P, C, R на кандидатах и сделках — по месяцам, классу (крипто / TradFi), стороне; дыры (нет значения) — число."""
    contracts = f36.read_contracts()
    W, S, R, day, d = trade_cols(side)
    mon = st.MONTH_OF_DAY[day]
    cls = np.array([float(contracts[s_]["tradfi"]) if s_ in contracts else float("nan") for s_ in S])
    out = {}
    for name, col in (("F", "gF"), ("P", "gP"), ("C", "conf02"), ("R", "rho")):
        v = np.isfinite(d[col])
        out[name] = dict(all=round(float(v.mean()), 4), holes=int((~v).sum()), by_month=[round(float(v[mon == k].mean()), 3) if (mon == k).any() else None for k in range(len(st.MONTH_LEN))],
                         crypto=round(float(v[cls == 0].mean()), 4) if (cls == 0).any() else None, tradfi=round(float(v[cls == 1].mean()), 4) if (cls == 1).any() else None,
                         n_tradfi=int((cls == 1).sum()), n_nocls=int(np.isnan(cls).sum()))
    return out


# ---- counts (без P&L) --------------------------------------------------------------------------------------------------------------

def n24_tab(wname):
    """Столбец `N24_q60` таблицы TK-638 по ключам (монета, мс)."""
    key = ("n24", wname)
    if key not in _C:
        _C[key] = r34.read_table_col("/data/p42-tk638/ftab38-h775/%s-L.csv" % wname, "N24_q60")
    return _C[key]


def cmd_counts(a):
    t_start = time.time()
    gates = {}
    say("== ворота данных (без P&L)")
    gates["sign"] = gate_sign()
    say("G-sign (условие 1): %s" % gates["sign"])
    gates["lvl"] = gate_lvl()
    say("G-lvl: %s%s" % ({k: v["miss"] for k, v in gates["lvl"]["res"].items()}, "" if gates["lvl"]["ok"] else "  СТОП, примеры %s" % gates["lvl"]["bad"]))
    gates["src"] = gate_src()
    say("G-src: файлов %d, без .sha256 %d, расхождений sha256 %d %s" % (gates["src"]["files"], gates["src"]["no_checksum"], len(gates["src"]["bad"]) - gates["src"]["no_checksum"], gates["src"]["bad"][:3]))
    gates["rest"] = gate_rest()
    say("G-rest: монет %d, строк %d, расхождения %s" % (gates["rest"]["coins"], gates["rest"]["rows"], gates["rest"]["bad"]))
    gates["causal"] = gate_causal()
    say("G-causal: строк %d (зерно %d), расхождений F %d, P %d, C %d" % (gates["causal"]["rows"], G_SEED, gates["causal"]["bad_F"], gates["causal"]["bad_P"], gates["causal"]["bad_C"]))
    write_json(q("res", "gates666.json"), stamp(gates))
    if not (gates["lvl"]["ok"] and gates["causal"]["ok"] and not gates["src"]["bad"] and not gates["rest"]["bad"]):
        raise GuardError("ворота данных не пройдены — стоп (см. выше, res/gates666.json)")
    allmasks, results, mon_by_side = {}, {}, {}
    for side in SIDES:
        W, S, R, day, d = trade_cols(side)
        mon = st.MONTH_OF_DAY[day]
        mon_by_side[side] = mon
        thr = thresholds(d)
        n_t = len(W)
        specs = cell_specs()
        cov = cov_table(side)
        res = dict(side=side, n_trades=int(n_t), thr=thr, coverage=cov, cells=[], jaccard={}, dup_keys={w.name: rows_of(w.name, side).dup_keys for w in st.TRADE_WINDOWS},
                   cand_rows={w.name: len(rows_of(w.name, side).sym) for w in st.TRADE_WINDOWS})
        say("== %s: сделок базы %d (по месяцам %s); кандидатов по окнам %s; дубли ключей %s" % (side, n_t, [int((mon == k).sum()) for k in range(len(st.MONTH_LEN))], res["cand_rows"], res["dup_keys"]))
        say("   пороги g (σ = %+d): F %s; P %s" % (SIGMA[side], {k: "%.3e" % v for k, v in thr["F"].items()}, {k: "%.3e" % v for k, v in thr["P"].items()}))
        for nm, c in cov.items():
            say("   покрытие %s: %.1f %% сделок, дыр %d, по месяцам %s, крипто %s, TradFi %s (n %d), без класса %d" % (nm, 100 * c["all"], c["holes"], c["by_month"], c["crypto"], c["tradfi"], c["n_tradfi"], c["n_nocls"]))
        share_merged = {t: round(float((d["conf" + t] == 1).mean()), 4) for t in ("01", "02", "04")}
        res["share_merged"] = share_merged
        say("   доля слитых (conf = 1) среди сделок базы: τ_c 0,1 %% %.1f %%, 0,2 %% %.1f %%, 0,4 %% %.1f %%" % (100 * share_merged["01"], 100 * share_merged["02"], 100 * share_merged["04"]))
        mb = mant_bucket(d["mant"])
        masks, seen = {}, {}
        for sp in specs:
            mk = excl_mask(sp, d, thr)
            key = np.packbits(mk).tobytes() + len(mk).to_bytes(8, "little")
            dup = seen.get(key)
            if dup is None:
                seen[key] = sp["cid"]
            masks[sp["cid"]] = mk
            adm, subset = admit_ex_ante(mk, mon)
            rem = [int(((mon == k) & ~mk).sum()) for k in range(len(st.MONTH_LEN))]
            res["cells"].append(dict(cid=sp["cid"], kind=sp["kind"], group=sp["group"], dup_of=dup, E=int(mk.sum()), kept_share=round(float(1 - mk.mean()), 4), subset=subset, admit_ex_ante=adm,
                                     excl_by_month=[int((mk & (mon == k)).sum()) for k in range(len(st.MONTH_LEN))], remain_by_month=rem,
                                     excl_by_mant=[int((mk & (mb == b)).sum()) for b in range(3)]))
        allmasks[side] = masks
        trial = [c for c in res["cells"] if c["kind"] == "T"]
        res["m_new_nodup"] = sum(1 for c in trial if c["dup_of"] is None)
        res["m_new_admit"] = sum(1 for c in trial if c["dup_of"] is None and c["admit_ex_ante"])
        for c in trial:
            nbs = [e for e in res["cells"] if e["kind"] == "N" and e["group"] == c["group"]]
            res["jaccard"][c["cid"]] = {e["cid"]: round(st.jaccard(masks[c["cid"]], masks[e["cid"]]), 3) for e in nbs}
        base_mb = [int((mb == b).sum()) for b in range(3)]
        res["base_by_mant"] = base_mb
        for c in res["cells"]:
            say("   клетка %-5s %s E=%d (оставлено %.1f %%%s) исключено по месяцам %s, останется %s, допуск %s%s" % (
                c["cid"], {"T": "пробная", "N": "сосед ", "V": "обратн."}[c["kind"]], c["E"], 100 * c["kept_share"], ", подмножество" if c["subset"] else "", c["excl_by_month"], c["remain_by_month"],
                "ok" if c["admit_ex_ante"] else "НЕТ", " (дубль %s)" % c["dup_of"] if c["dup_of"] else ""))
        for cid, jd in res["jaccard"].items():
            good = sum(1 for v in jd.values() if v <= st.JACCARD_MAX)
            say("   Jaccard %-4s %s; соседей с J ≤ 0,8: %d%s" % (cid, jd, good, "" if good >= 2 else "  ← плато пройти не может (g5)"))
        c_r = next(c for c in res["cells"] if c["cid"] == "R05")
        say("   условие 2 (R05): доля исключённых сделок базы по корзинам мантиссы цены %s: %s (исключено %s из %s)" % (
            MB_NAMES, [("%.1f %%" % (100 * e / b) if b else "—") for e, b in zip(c_r["excl_by_mant"], base_mb)], c_r["excl_by_mant"], base_mb))
        results[side] = res
        # таблицы `--ftab` по кандидатам окон
        for w in st.TRADE_WINDOWS:
            rows = rows_of(w.name, side)
            dr = derive(rows.raw, side)
            cols_t = {sp["cid"]: np.where(excl_mask(sp, dr, thr), 0.0, 1.0) for sp in specs}
            cols_t["G_base"] = np.ones(len(rows.sym))
            n = write_cell_table(q("ftab666", "%s-%s.csv" % (w.name, side)), rows.sym, rows.rel, cols_t)
            say("   ftab666 %s-%s: %d строк, колонок %d" % (w.name, side, n, len(cols_t)))
            if side == "L":
                n24 = n24_tab(w.name)
                try:
                    ref = np.array([n24[(s_, int(r_))] for s_, r_ in zip(rows.sym, rows.rel)])
                except KeyError as e:
                    raise GuardError("L_n24: кандидата %s нет в таблице TK-638 (%s)" % (e, w.name))
                comp = {"N24_REF": ref}
                for cid in ("F25", "P25", "C02", "R05"):
                    comp["N24_" + cid] = np.where((ref == 1.0) & (cols_t[cid] == 1.0), 1.0, 0.0)
                write_cell_table(q("ftab666-n24", "%s-L.csv" % w.name), rows.sym, rows.rel, comp)
    m_new = sum(r["m_new_nodup"] for r in results.values())
    say("== фактическое m_new (пробные клетки без дублей, обе стороны) = %d; с допуском до P&L %d; семейство «вход» m = %d + %d = %d; первый шаг Холма %.5f" % (
        m_new, sum(r["m_new_admit"] for r in results.values()), N_OLD_TRIAL, m_new, N_OLD_TRIAL + m_new, ALPHA / (N_OLD_TRIAL + m_new)))
    for side in SIDES:
        write_json(q("res", "counts666-%s.json" % side), stamp(r33.jsonable(results[side])))
    write_json(q("res", "counts666-summary.json"), stamp(dict(m_new=m_new, m_family=N_OLD_TRIAL + m_new, seconds=round(time.time() - t_start, 1))))
    say("counts готово: код %s — запись в протокол §13.66.1 (psec add --prereg) до чтения P&L" % code_id())


# ---- после PASS критика: клетки, G8, выбор (читают P&L) ---------------------------------------------------------------------------

def need_pass():
    if not os.path.exists(q("PASS")):
        raise GuardError("нет /data/p42-tk666/PASS — P&L не читается")


def run_specs(side):
    """Клетки, которые гоняет движок: без дублей (из counts) + `G_base` (все 1,0: движок = база побайтно)."""
    p = q("res", "counts666-%s.json" % side)
    dup = {c["cid"]: c["dup_of"] for c in json.load(open(p, encoding="utf-8"))["cells"]}
    return [sp["cid"] for sp in cell_specs() if dup.get(sp["cid"]) is None] + ["G_base"]


def cells_dir(w, side, cid):
    return os.path.join(ROOT, "cells", "%s-%s" % (w, side), cid)


def n24_dir(w, cid):
    return os.path.join(ROOT, "cells-n24", "%s-L" % w, cid)


def cmd_cells(a):
    need_pass()
    u = uni()
    names = [a.window] if a.window else [w.name for w in st.TRADE_WINDOWS]
    for name in names:
        w = r4.win(name)
        for side in SIDES:
            out = os.path.join(ROOT, "cells", "%s-%s" % (w.name, side))
            if not os.path.exists(out + "/DONE"):
                ftab = q("ftab666", "%s-%s.csv" % (w.name, side))
                variants = [(cid, "%s%s fa=%s fo=lo fv=%s" % (BASE[side], u.extra, cid, FV_PASS)) for cid in run_specs(side)]
                r5.tick(u, w, variants, out, {}, q("log", "cells-%s-%s.log" % (w.name, side)), ftab=ftab)
                open(out + "/DONE", "w", encoding="utf-8").write("%s %s" % (code_id(), r4.sha256(ftab)))
        out = os.path.join(ROOT, "cells-n24", "%s-L" % w.name)
        if not os.path.exists(out + "/DONE"):
            ftab = q("ftab666-n24", "%s-L.csv" % w.name)
            variants = [(cid, "%s%s fa=%s fo=lo fv=%s" % (BASE["L"], u.extra, cid, FV_PASS)) for cid in ("N24_REF", "N24_F25", "N24_P25", "N24_C02", "N24_R05")]
            r5.tick(u, w, variants, out, {}, q("log", "cells-n24-%s.log" % w.name), ftab=ftab)
            open(out + "/DONE", "w", encoding="utf-8").write("%s %s" % (code_id(), r4.sha256(ftab)))
        say("cells %s готово" % w.name)


def read_dump_keys(w, side):
    import tk604_feat as tf
    dsym, drel = tf.read_sigsx_keys(os.path.join(uni().base(w.name, side), ENG[side] + "-sigsx.csv"))
    return [(s_, int(r_)) for s_, r_ in zip(dsym, drel)]


def trade_bytes_sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def cmd_g8(a):
    """G8: `no_key = 0` и `dropped` движка = пересчёту по таблице (значение < 0,5) во всех прогонах; G-base: `G_base` = база побайтно; G-ref: `N24_REF` = `L_n24` TK-638 побайтно."""
    need_pass()
    u = uni()
    bad, res = [], {}

    def check(ftab, cid, dkeys):
        tab = r34.read_table_col(ftab, cid)
        miss = sum(1 for k in dkeys if k not in tab)
        exp_u = sum(1 for v in tab.values() if v == v and v < FV_PASS)
        exp_m = sum(1 for k in dkeys if k in tab and tab[k] < FV_PASS)
        return miss, exp_u, exp_m

    for w in st.TRADE_WINDOWS:
        for side in SIDES:
            ftab = q("ftab666", "%s-%s.csv" % (w.name, side))
            dkeys = read_dump_keys(w, side)
            for cid in run_specs(side):
                miss, exp_u, exp_m = check(ftab, cid, dkeys)
                m = json.load(open(os.path.join(cells_dir(w.name, side, cid), "meta.json"), encoding="utf-8"))[ENG[side]]
                fc = r4.filter_counters(m)
                res["%s/%s/%s" % (w.name, side, cid)] = dict(counters=fc, expected_unique=exp_u, expected_rows=exp_m, dump_without_row=miss)
                if miss or fc is None or fc["no_key"] != 0 or fc["dropped"] not in (exp_u, exp_m):
                    bad.append("%s/%s/%s: %s, ожидалось dropped %d / %d, строк дампа без строки таблицы %d" % (w.name, side, cid, fc, exp_u, exp_m, miss))
        ftab = q("ftab666-n24", "%s-L.csv" % w.name)
        dkeys = read_dump_keys(w, "L")
        for cid in ("N24_REF", "N24_F25", "N24_P25", "N24_C02", "N24_R05"):
            miss, exp_u, exp_m = check(ftab, cid, dkeys)
            m = json.load(open(os.path.join(n24_dir(w.name, cid), "meta.json"), encoding="utf-8"))[ENG["L"]]
            fc = r4.filter_counters(m)
            res["%s/n24/%s" % (w.name, cid)] = dict(counters=fc, expected_unique=exp_u, expected_rows=exp_m, dump_without_row=miss)
            if miss or fc is None or fc["no_key"] != 0 or fc["dropped"] not in (exp_u, exp_m):
                bad.append("%s/n24/%s: %s, ожидалось dropped %d / %d, строк дампа без строки таблицы %d" % (w.name, cid, fc, exp_u, exp_m, miss))
    base_ref = {}
    for w in st.TRADE_WINDOWS:
        for side in SIDES:
            a_ = trade_bytes_sha(os.path.join(cells_dir(w.name, side, "G_base"), ENG[side] + "-trades.csv"))
            b_ = trade_bytes_sha(os.path.join(u.base(w.name, side), ENG[side] + "-trades.csv"))
            base_ref["%s-%s" % (w.name, side)] = a_ == b_
            if a_ != b_:
                bad.append("G-base %s-%s: сделки G_base ≠ база" % (w.name, side))
        a_ = trade_bytes_sha(os.path.join(n24_dir(w.name, "N24_REF"), ENG["L"] + "-trades.csv"))
        b_ = trade_bytes_sha(os.path.join(p8.kind_dir(u, "ladd", w.name, "L", "N24_q60"), ENG["L"] + "-trades.csv"))
        base_ref["%s-n24" % w.name] = a_ == b_
        if a_ != b_:
            bad.append("G-ref %s: сделки N24_REF ≠ L_n24 TK-638" % w.name)
    write_json(q("gates", "g8.json"), dict(res=res, base_ref=base_ref, bad=bad))
    say("G8 / G-base / G-ref", "FAIL " + "; ".join(bad[:8]) if bad else "ok: no_key = 0 и dropped = пересчёту во всех %d прогонах; G-base и G-ref побайтно %s" % (len(res), base_ref))
    if bad:
        sys.exit(1)


def trade_kinds_pnl(side):
    key = ("kp", side)
    if key not in _C:
        u = uni()
        K, P = [], []
        for w in st.TRADE_WINDOWS:
            t = st.read_trades_csv(os.path.join(u.base(w.name, side), ENG[side] + "-trades.csv"))
            d = st.abs_day(w, t.entry_ms)
            lo_i, hi_i = (w.lod - st.D0).days, (w.hid - st.D0).days
            for i in np.nonzero((d >= lo_i) & (d <= hi_i))[0]:
                K.append(t.kind[i])
                P.append(float(t.gross[i] - t.fees[i]))
        _C[key] = (np.array(K, dtype=object), np.array(P, dtype=float))
    return _C[key]


def base_series(side):
    """Ряд сделок базы + охрана порядка и P&L (как tk636)."""
    u = uni()
    W, S, R = r34.trade_rows(u, side)
    ser = r5.load_series(u, side, "base")
    t0 = {w.name: w.t0_ms for w in st.TRADE_WINDOWS}
    if list(ser.sym_name) != list(S) or not np.array_equal(ser.key_ms, R + np.array([t0[w] for w in W], dtype=np.int64)):
        raise GuardError("%s: порядок сделок базы не совпал с рядом load_series" % side)
    kinds, pnl = trade_kinds_pnl(side)
    if len(pnl) != len(ser.val) or not np.allclose(pnl, ser.val, atol=1e-6):
        raise GuardError("%s: P&L сделок из файла ≠ val ряда" % side)
    return ser, kinds, pnl


def load_cell_series(side, cid):
    u = uni()
    tables, daily = {}, {}
    for w in st.TRADE_WINDOWS:
        dd = cells_dir(w.name, side, cid)
        tables[w.name] = st.read_trades_csv(os.path.join(dd, ENG[side] + "-trades.csv"))
        daily[w.name] = st.read_daily_csv(os.path.join(dd, ENG[side] + "-daily.csv"))
    return st.splice(tables, u.coins(), daily=daily)


def build_side(side):
    """SideCells стороны: база + клетки (пробные T, соседи N, обратные V; дубль — без ряда). Охрана: пороги из counts = пересчёту."""
    ser, kinds, pnl = base_series(side)
    W, S, R, day, d = trade_cols(side)
    if not np.array_equal(day, ser.day):
        raise GuardError("%s: сутки сделок в counts ≠ ряд" % side)
    thr = thresholds(d)
    counts = json.load(open(q("res", "counts666-%s.json" % side), encoding="utf-8"))
    if counts["thr"] != json.loads(json.dumps(thr)):
        raise GuardError("%s: пороги counts ≠ пересчёту" % side)
    dup = {c["cid"]: c["dup_of"] for c in counts["cells"]}
    cells = []
    for sp in cell_specs():
        mk = excl_mask(sp, d, thr)
        series = None if dup[sp["cid"]] is not None else load_cell_series(side, sp["cid"])
        cells.append(s5.Cell(sp["cid"], "-", sp["group"], 0.0, None, sp["kind"] == "T", dup[sp["cid"]], series, mk))
    return s5.SideCells(ser, cells, len(uni().coins()))


def admitted_666(r):
    """Допуск п. 6 по строке `cell_row`: база ≥ 20 в месяце (01–13.11 — 8), клетка ≥ 20 / 8; подмножество (остаётся < 50 %) — ≥ 70 сделок за окно и ≥ 8 в каждом месяце; исключено ≥ 30."""
    mt = r["month"]
    base_ok = all(nr >= st.MIN_TRADES[k] for k, (_, nc, nr) in mt.items())
    subset = r["n_r"] > 0 and r["n_c"] < SUBSET_SHARE * r["n_r"]
    if subset:
        cell_ok = r["n_c"] >= SUBSET_MIN_TOTAL and all(nc >= SUBSET_MIN_MONTH for _, nc, _ in mt.values())
    else:
        cell_ok = all(nc >= st.MIN_TRADES[k] for k, (_, nc, nr) in mt.items())
    return bool(base_ok and cell_ok and r["a2"]), subset


def evaluate_666(sides, om, ps_old):
    """Ворота (g1)–(g5): допуск п. 6 (подмножества), смещённый Холм по семейству m = 61 + m_new (p прежних — как есть), плато по соседям группы. Обратные клетки (V) считаются, в Холм не входят."""
    om = np.asarray(om)
    rows = {}
    for s, sd in sides.items():
        rows[s] = {}
        for c in sd.cells:
            if c.series is None:
                continue
            r = s5.cell_row(sd, c, om)
            r["admitted"], r["subset"] = admitted_666(r) if r["n_c"] > 0 and r["n_r"] > 0 else (False, False)
            r["kept_share"] = r["n_c"] / r["n_r"] if r["n_r"] else float("nan")
            rows[s][c.cid] = r
    trial = [(s, c.cid) for s, sd in sides.items() for c in sd.cells if c.trial and c.series is not None]
    ps_new = [rows[s][cid]["p"] if rows[s][cid]["admitted"] else 1.0 for s, cid in trial]
    rej_all = st.holm_reject(list(ps_old) + ps_new, ALPHA)[len(ps_old):]
    rej_new = st.holm_reject(ps_new, ALPHA) if ps_new else []
    rej_side = {}
    for s in sides:
        idx = [i for i, (ss, _) in enumerate(trial) if ss == s]
        for i, r_ in zip(idx, st.holm_reject([ps_new[i] for i in idx], ALPHA) if idx else []):
            rej_side[i] = bool(r_)
    for i, (s, cid) in enumerate(trial):
        r = rows[s][cid]
        ok = bool(r["admitted"] and r["d"] > 0)
        r["g1"], r["g1_new_only"], r["g1_side"] = bool(rej_all[i] and ok), bool(rej_new[i] and ok), bool(rej_side.get(i) and ok)
    passed = []
    for s, sd in sides.items():
        by = {c.cid: c for c in sd.cells}
        for c in sd.cells:
            if not (c.trial and c.series is not None):
                continue
            r = rows[s][c.cid]
            good, det = [], {}
            for nb in sd.cells:
                if nb.trial or nb.D != c.D or nb.cid in V_CIDS:
                    continue
                src = nb if nb.series is not None else by.get(nb.dup_of)
                if src is None or src.series is None or src.cid not in rows[s]:
                    det[nb.cid] = None
                    continue
                rn = rows[s][src.cid]
                j = st.jaccard(r["mask"], nb.excl)
                det[nb.cid] = (round(rn["d"], 4), round(j, 3), rn["admitted"])
                if rn["admitted"] and rn["d"] == rn["d"] and rn["d"] >= st.PLATEAU * r["d"] and j <= st.JACCARD_MAX:
                    good.append(nb.cid)
            r["g5"] = bool(r["admitted"] and r["d"] > 0 and len(good) >= s5.NEIGHBOUR_PLATEAU_MIN)
            r["g5_note"] = "соседи (Δ, Jaccard, допущен) %s; порог Δ %.4f; зачтены %s" % (det, st.PLATEAU * r["d"] if r["d"] == r["d"] else float("nan"), good)
            r["pass"] = bool(r["admitted"] and r["g1"] and r.get("g2") and r.get("g3") and r.get("g4") and r["g5"])
            if r["pass"]:
                passed.append((s, c.cid))
    return dict(rows=rows, holm=dict(m_new=len(trial), ps=ps_new, trial=trial, ps_old=list(ps_old), m_family=len(ps_old) + len(trial)), passed=passed)


def phi_inv(p):
    from statistics import NormalDist
    return NormalDist().inv_cdf(p)


def expected_max_abs_z(n):
    """E[max |Z|] из n независимых N(0,1): ∫₀^∞ (1 − (2Φ(x) − 1)ⁿ) dx (численно)."""
    xs = np.linspace(0.0, 8.0, 80001)
    cdf = np.array([math.erf(x / math.sqrt(2.0)) for x in xs])
    y = 1.0 - cdf ** n
    return float(((y[1:] + y[:-1]) / 2.0 * np.diff(xs)).sum())


def outcome_of(r, mde, side_rows):
    """Исход клетки-гипотезы по п. 7: добавляет $ / качество выше, Σ не выше / не работает / не ясно."""
    if r is None or not r["admitted"]:
        return "не ясно: клетка не допущена (счёт сделок)"
    if r["pass"]:
        return "добавляет $ (найдено на этих данных; кандидат только вперёд)"
    if r["g1"] and r.get("g2") and r.get("g4") and not r.get("g3"):
        return "качество сделок выше, Σ не выше (подмножество; не добавляет $)"
    if r["d"] < 0:
        return "не работает"
    return "не ясно: |Δ| < MDE %.2f $" % mde


def cell_stats_full(ser, kinds, pnl):
    r = p8.cell_stats(ser, kinds, pnl)
    return r


def group_ctx_ps():
    """Контекст (п. 6): 53 прежних p (TK-648 — 12, TK-651 — 17, TK-656 — 24), если читаются из готовых json; иначе None."""
    return None


def cmd_select(a):
    need_pass()
    u = uni()
    om = np.arange(st.NDAYS)
    ps_old = []
    for p_ in OLD_SELECT:
        ps_old += json.load(open(p_, encoding="utf-8"))["holm"]["ps"]
    if len(ps_old) != N_OLD_TRIAL:
        raise GuardError("прежних пробных клеток %d ≠ %d (п. 6)" % (len(ps_old), N_OLD_TRIAL))
    sides = {s: build_side(s) for s in SIDES}
    ev = evaluate_666(sides, om, ps_old)
    m_family = ev["holm"]["m_family"]
    z_holm = phi_inv(1.0 - ALPHA / m_family)
    bars = {n: expected_max_abs_z(n) for n in N_GATE}
    slices = {}
    for s, sd in sides.items():
        ser, kinds, pnl = base_series(s)
        slices[s] = {"база": cell_stats_full(ser, kinds, pnl)}
        for c in sd.cells:
            c2 = r33.with_series(sd, c)
            if c2 is not None:
                slices[s][c.cid] = cell_stats_full(*load_cell_series_kp(s, c2.cid if c2.dup_of is None else c2.dup_of))
    outcomes, mde = {}, {}
    for s in SIDES:
        for f, name, cid in HYPS:
            r = ev["rows"][s].get(cid)
            mde[(s, cid)] = (z_holm + 0.84) * r["se"] if r is not None and r["se"] == r["se"] else float("nan")
            outcomes[(s, cid)] = outcome_of(r, mde[(s, cid)], ev["rows"][s])
    say("Холм (g1): семейство m = %d (%d прежних + %d новых пробных); z_Холм %.2f; шумовая планка E[max|z|] N=32 %.2f, N=93 %.2f" % (m_family, len(ps_old), ev["holm"]["m_new"], z_holm, bars[32], bars[93]))
    for s in SIDES:
        b = slices[s]["база"]
        say("== %s: R:R базы %.2f, стоп %.2f $ (%.2f %% маржи); база %d сделок, Σ %+.2f $, %+.3f $/сделку, Sharpe %.2f" % (
            s, b["rr"]["rr"], b["rr"]["stops"]["mean_loss"], b["rr"]["stops"]["mean_loss_pct"], b["n"], b["sum"], b["mean"], b["sharpe"]))
        for cid, r in ev["rows"][s].items():
            sl = slices[s].get(cid) or {}
            say("   %-5s %-6s: Δ %s ± %s $/сд., z %s, p %s, n_c %d, исключено %d, Σ %s, Sharpe %s, R:R %s, допуск %s%s, g1 %s g2 %s g3 %s g4 %s g5 %s%s" % (
                cid, "пробная" if r["trial"] else "справ.", "%.3f" % r["d"], "%.3f" % r["se"], "%.2f" % r["z"], "%.4f" % r["p"], r["n_c"], r["n_ex"], "%.0f" % sl["sum"] if sl else "—",
                "%.2f" % sl["sharpe"] if sl else "—", "%.2f" % sl["rr"]["rr"] if sl else "—", r["admitted"], " (подмножество)" if r["subset"] else "", r.get("g1"), r.get("g2"), r.get("g3"), r.get("g4"), r.get("g5"),
                " ПРОШЛА" if r.get("pass") else ""))
    for (s, cid), o in outcomes.items():
        say("ИСХОД %s %s: %s" % (SIDE_NAME_RU[s], cid, o))
    out = dict(holm=ev["holm"], passed=ev["passed"], rows=ev["rows"], outcomes={"%s/%s" % k: v for k, v in outcomes.items()}, mde={"%s/%s" % k: v for k, v in mde.items()},
               z_holm=z_holm, noise_bars=bars, slices=slices)
    write_json(q("res", "select666.json"), stamp(r33.jsonable(out)))
    say("метка: код %s" % code_id())


def load_cell_series_kp(side, cid):
    """(ряд, виды выходов, P&L) клетки стороны по каталогам клеток."""
    u = uni()
    dirs = {w.name: (cells_dir(w.name, side, cid), side) for w in st.TRADE_WINDOWS}
    return p8.load_cell(u, side, dirs)


COMMANDS = {"levels": cmd_levels, "counts": cmd_counts, "cells": cmd_cells, "g8": cmd_g8, "select": cmd_select}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=sorted(COMMANDS))
    ap.add_argument("window", nargs="?")
    a = ap.parse_args()
    try:
        COMMANDS[a.cmd](a)
    except GuardError as e:
        say("СТОП:", e)
        sys.exit(2)


if __name__ == "__main__":
    main()
