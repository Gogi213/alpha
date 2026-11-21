#!/usr/bin/env python3
"""TK-664: два недостающих числа на ГОТОВЫХ сделках П-42 (775 контрактов, 01.07–13.11, 500 $, после комиссий): (1) фандинг за время удержания, (2) занятый капитал / число одновременных позиций.
Движок не запускается, сделки не пересчитываются (В-213): те же ряды, что у tk662_equity.py / tk663_shape.py (S_base, S_mk1, A_n_1, L_base, L_n24, L_mk2); сверка n и Σ с карточкой TK-662 — расхождение = остановка.
Фандинг (модель, на допущениях — в отчёте): выплата на биржевой отметке `ft` (Binance USDT-M, фактические отметки, интервал свой у символа) платится, если вход строго раньше и выход строго позже отметки (как `settle_tick_mk`: `fms > entry && fms < exit`,
отметка округлена вниз до минуты); сумма = −s · ставка · 500 $ · (марк / цена входа), s = +1 лонг / −1 шорт (+ = получили). Марк: сентябрь–ноябрь — `mark_price` из `/data/klines/binance/<SYM>/funding.csv`; июль–август (там марка нет) — open минутки `ft` из 1m свечей `/data/p42-tk596/{jul,aug}/klines`;
пригодность замены проверяется на сентябре–ноябре (свечи против `mark_price`). Допущение «вся позиция 500 $ открыта от входа до выхода»: частичные тейки и поздний второй лот движок в CSV не пишет — выплаты считаются от полного лота.
Поле `funding` самого движка в CSV почти пустое (7 ненулевых строк из 435 у S_base) — используется только как справка.
Капитал: интервал позиции [вход, выход), маржа 500 $ / плечо 2 = 250 $ на позицию; развёртка событий (выход раньше входа в ту же мс); распределение по времени (доля 136 суток на каждом числе позиций) и в моменты входа (сколько открыто, считая новую).
Депозит «без пропусков сигналов»: D1 = 250 $ × максимум одновременных позиций (P&L не считается); D2 = минимальный депозит, при котором депозит + реализованный P&L закрытых сделок (после фандинга) покрывает маржу в любой момент (нереализованный P&L не учтён).
В прогонах лимита позиций нет (`rej[cap]` = 0 в meta.json) — число одновременных позиций это спрос без отказов.
Запуск на srv: /opt/venv/bin/python tk664_funding_capital.py <код TK-639 (/data/p42-tk639/code)> <каталог выхода>
Выход: funding-<линия>.csv (сделка, выплаты, $), concurrency-<линия|пара>.csv (число позиций → часы), summary.json, tables.md. Только описание, без вердикта и без подбора фильтров."""
import csv
import datetime as dt
import json
import os
import sys
import zipfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk663_shape as t63  # noqa: E402  (LINES, sharpe, num, table — сверка со сделками TK-662 та же)

NOTIONAL = 500.0
LEVERAGE = 2.0
MARGIN = NOTIONAL / LEVERAGE            # 250 $ на позицию
MIN_MS = 60_000
DAY_MS = 86_400_000
D0 = dt.date(2026, 7, 1)
D0_MS = int(dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
END_MS = int(dt.datetime(2026, 11, 14, tzinfo=dt.timezone.utc).timestamp() * 1000)     # первая мс 14.11 — дальше не читаем (изоляция G6)
SEP_MS = int(dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
FUND_ZIPS = "/data/p42-metrics/funding"                  # <SYM>/<SYM>-fundingRate-2026-07|08|09.zip (calc_time, interval, rate)
FUND_API = "/data/klines/binance"                         # <SYM>/funding.csv: funding_time, rate, mark_price (с 01.09)
FUND_EXTRA = "/data/p42-tk664/funding-extra"              # докачка tk664_fetch_missing.py: те же колонки, 01.07–13.11 (монеты, которых нет на складе)
KL_JA = {7: "/data/p42-tk596/jul/klines", 8: "/data/p42-tk596/aug/klines"}
KL_SN = "/data/klines/binance"
PAIRS = {"L_base+S_base": ("L_base", "S_base"), "L_n24+A_n_1": ("L_n24", "A_n_1")}
ORDER = ["S_base", "S_mk1", "A_n_1", "L_base", "L_n24", "L_mk2"]
NVAL = 3000                  # платежей сентября–ноября для проверки замены марка свечой
SEED = 664

num, table, sharpe = t63.num, t63.table, t63.sharpe


# ───────────────────────────── сделки ─────────────────────────────

def load_lines(code):
    """Как tk663_shape.load_lines (те же каталоги, сверка с TK-662), плюс цена входа (entry_px_e8), число тейков и поле funding движка из CSV; порядок строк — как в `st.splice`."""
    sys.path.insert(0, code)
    import tk604_stats as st   # noqa: E402
    import tk625_run as r5     # noqa: E402
    import tk638_post as p8    # noqa: E402
    u = r5.Uni("h775")
    UNI = json.load(open("/data/pages/ml/data/universe.json", encoding="utf-8"))
    assert UNI["days"] == st.NDAYS and UNI["d0"] == str(st.D0), "окно universe.json / tk604_stats разное"
    W = [w.name for w in st.TRADE_WINDOWS]
    gmk = "/data/p42-tk626/gmk/out"
    dirs = {
        "S_b775": lambda w: "/data/p42-tk621/out/all_t24/%s/S_base" % w,
        "S_mk775": lambda w: os.path.join(gmk, w, "cd0838447e287"),
        "S_an1": lambda w: "/data/p42-tk657/cells/%s-S/A_n_1" % w,
        "L_b775": lambda w: "/data/p42-tk621/out/all_t24/%s/L_base" % w,
        "L_n24": lambda w: p8.kind_dir(u, "ladd", w, "L", "N24_q60"),
        "L_mk2": lambda w: os.path.join(gmk, w, "cf280a1e8fc7c"),
    }
    g7 = {"S_an1"}
    res = {}
    for name, (key, role, (n_ref, s_ref), tol) in t63.LINES.items():
        side = "S" if name.startswith(("S_", "A_")) else "L"
        d = {w: (dirs[key](w), side) for w in W}
        tables, daily = p8.read_tables(d)
        if key in g7:      # G7 §13.43 — как в tk662_equity
            for w in st.TRADE_WINDOWS:
                tb, dl = tables[w.name], daily[w.name]
                assert abs(float(sum(dl.values())) - float(tb.net.sum())) <= 1e-4 + 2e-6 * len(tb.net), ("G7 окна", w.name, side)
            ser = st.splice(tables, u.coins(), daily=None)
        else:
            ser = st.splice(tables, u.coins(), daily=daily)
        kinds, pnl = p8.kinds_pnl(tables)
        assert len(pnl) == len(ser.val) and np.allclose(pnl, ser.val, atol=1e-6), (name, "P&L сделок ≠ val ряда")
        n, s = len(ser.val), float(np.sum(ser.val))
        assert n == n_ref and abs(s - s_ref) < tol, ("сверка с карточкой TK-662", name, n, n_ref, s, s_ref)
        px, tk, ef = [], [], []
        for w in st.TRADE_WINDOWS:     # те же строки, что выбирает splice: по дню входа в зачётном диапазоне
            t = tables[w.name]
            raw = pd.read_csv(os.path.join(d[w.name][0], p8.ENG[side] + "-trades.csv"))
            assert len(raw) == len(t) and (raw.sym.to_numpy() == np.array(t.sym)).all() and (raw.entry_ms.to_numpy() == t.entry_ms).all(), ("CSV ≠ таблица", name, w.name)
            dd = st.abs_day(w, t.entry_ms)
            lo_i, hi_i = (w.lod - st.D0).days, (w.hid - st.D0).days
            sel = np.nonzero((dd >= lo_i) & (dd <= hi_i))[0]
            px.append(raw.entry_px_e8.to_numpy(np.int64)[sel])
            tk.append(raw.takes.to_numpy(np.int64)[sel])
            ef.append(raw.funding.to_numpy(float)[sel])
        df = pd.DataFrame({"sym": [str(x) for x in ser.sym_name], "t_in": np.asarray(ser.t_in, np.int64), "t_out": np.asarray(ser.t_out, np.int64),
                           "day": np.asarray(ser.day, np.int64), "pnl": np.asarray(ser.val, float), "kind": [str(k) for k in kinds], "side": side,
                           "px": np.concatenate(px) / 1e8, "takes": np.concatenate(tk), "eng_fund": np.concatenate(ef)})
        assert len(df) == n and (df.px > 0).all()
        res[name] = df
    return res, st.NDAYS


# ───────────────────────────── данные фандинга ─────────────────────────────

def read_zip_rates(sym):
    out = {}
    for m in ("2026-07", "2026-08", "2026-09"):
        p = os.path.join(FUND_ZIPS, sym, "%s-fundingRate-%s.zip" % (sym, m))
        if not os.path.exists(p):
            continue
        with zipfile.ZipFile(p) as z, z.open(z.namelist()[0]) as f:
            for row in csv.DictReader(line.decode("utf-8") for line in f):
                out[int(row["calc_time"])] = float(row["last_funding_rate"])
    return out


def read_api(sym):
    p = os.path.join(FUND_API, sym, "funding.csv")
    if not os.path.exists(p):
        p = os.path.join(FUND_EXTRA, sym, "funding.csv")
    if not os.path.exists(p):
        return {}
    d = pd.read_csv(p)
    return {int(t): (float(r), float(m)) for t, r, m in zip(d.funding_time, d.funding_rate, d.mark_price)}


def build_funding(syms):
    """{sym: (fms [минута, мс], rate, mark [NaN = нет], src)}; отметки < 14.11. Сентябрь: api (`mark_price`) главнее, zip — сверка ставок."""
    out, cover, overlap = {}, {"zip": 0, "api": 0, "both": 0, "none": []}, {"n": 0, "bad": 0}
    for s in sorted(syms):
        z, a = read_zip_rates(s), read_api(s)
        if not (z or a):
            cover["none"].append(s)
            continue
        cover["zip" if z and not a else "api" if a and not z else "both"] += 1
        recs = {}
        for t, r in z.items():
            recs[t] = (r, float("nan"))
        for t, (r, m) in a.items():
            if t in z:
                overlap["n"] += 1
                overlap["bad"] += int(abs(z[t] - r) > 1e-9)
            recs[t] = (r, m)
        ts = np.array(sorted(t for t in recs if t < END_MS), dtype=np.int64)
        out[s] = (ts // MIN_MS * MIN_MS, np.array([recs[t][0] for t in ts]), np.array([recs[t][1] for t in ts]))
    return out, cover, overlap


def kline_opens(sym, fms_list):
    """open минутки fms (мс) из 1m свечей: {fms: open}; нет файла / минуты — NaN. Файл читается раз на (монета, сутки)."""
    out = {}
    by_day = {}
    for f in fms_list:
        by_day.setdefault(int(f) // DAY_MS, []).append(int(f))
    for dd, fs in by_day.items():
        day = dt.datetime.fromtimestamp(dd * DAY_MS / 1000, dt.timezone.utc)
        root = KL_JA.get(day.month) if day.month in KL_JA else KL_SN
        p = os.path.join(root, sym, day.strftime("%Y-%m-%d") + ".csv")
        if not os.path.exists(p):
            p = os.path.join(KL_SN, sym, day.strftime("%Y-%m-%d") + ".csv")
        if not os.path.exists(p):
            for f in fs:
                out[f] = float("nan")
            continue
        k = pd.read_csv(p, usecols=["open_time", "open"])
        ot, op = k.open_time.to_numpy(np.int64), k.open.to_numpy(float)
        for f in fs:
            i = int(np.searchsorted(ot, f))
            out[f] = float(op[i]) if i < len(ot) and ot[i] == f else float("nan")
    return out


# ───────────────────────────── фандинг по сделкам ─────────────────────────────

def pay_ranges(df, fund):
    """Для каждой сделки: [a, b) — индексы выплат символа строго между входом и выходом."""
    a, b = np.zeros(len(df), np.int64), np.zeros(len(df), np.int64)
    for s, ix in df.groupby("sym").indices.items():
        if s not in fund:
            continue
        fms = fund[s][0]
        a[ix] = np.searchsorted(fms, df.t_in.to_numpy()[ix], side="right")
        b[ix] = np.searchsorted(fms, df.t_out.to_numpy()[ix], side="left")
    return a, np.maximum(a, b)


def funding_trades(df, fund, mark, mode, part=None):
    """mode: 'mark' — сумма ставка·(марк/цена входа)·500 · (−s); 'flat' — марк = цена входа; part: 'ja' — только выплаты до 01.09 (июль–август), 'sn' — с 01.09 (там у движка есть своё поле funding), None — все.
    Возвращает (итог, получено, уплачено, число выплат, без данных монеты)."""
    n = len(df)
    tot, rec, paid, cnt = np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n, np.int64)
    s = np.where(df.side.to_numpy() == "L", 1.0, -1.0)
    a, b = pay_ranges(df, fund)
    nodata = np.array([x not in fund for x in df.sym], bool)
    for sym, ix in df.groupby("sym").indices.items():
        if sym not in fund:
            continue
        fms, rate, _ = fund[sym]
        mk = mark[sym] if mode == "mark" else np.ones(len(fms))
        sel = np.ones(len(fms), bool) if part is None else (fms < SEP_MS if part == "ja" else fms >= SEP_MS)
        rm = np.where(np.isnan(mk) | ~sel, 0.0, rate * mk)    # выплаты вне диапазонов сделок марка не имеют (NaN) — в сумму не входят; нужные имеют (`fill_marks`: none = 0)
        cum = np.concatenate([[0.0], np.cumsum(rm)])
        cpos = np.concatenate([[0.0], np.cumsum(np.maximum(rm, 0.0))])
        cneg = np.concatenate([[0.0], np.cumsum(np.minimum(rm, 0.0))])
        k = NOTIONAL / df.px.to_numpy()[ix] if mode == "mark" else np.full(len(ix), NOTIONAL)
        S = cum[b[ix]] - cum[a[ix]]
        P = cpos[b[ix]] - cpos[a[ix]]
        N = cneg[b[ix]] - cneg[a[ix]]
        sg = s[ix]
        tot[ix] = -sg * k * S
        # платёж = −s·rm·k: плюс при sg·rm < 0
        rec[ix] = np.where(sg > 0, -k * N, k * P)
        paid[ix] = np.where(sg > 0, -k * P, k * N)
        cnt[ix] = np.concatenate([[0], np.cumsum(sel)])[b[ix]] - np.concatenate([[0], np.cumsum(sel)])[a[ix]]
    return tot, rec, paid, cnt, nodata


def fill_marks(df_all, fund):
    """Марк на каждую выплату: api `mark_price`, где есть; иначе open минутки из свечей (только выплаты, нужные сделкам). Возвращает ({sym: массив марка}, статистика)."""
    need = {}
    for df in df_all:
        a, b = pay_ranges(df, fund)
        for sym, ix in df.groupby("sym").indices.items():
            if sym in fund:
                m = need.setdefault(sym, np.zeros(len(fund[sym][0]), bool))
                for i in ix:
                    m[a[i]:b[i]] = True
    mark, st = {}, {"api": 0, "kline": 0, "none": 0, "payments": 0, "kl_files_sym": 0}
    for sym, m in need.items():
        fms, rate, mk = fund[sym]
        mk = mk.copy()
        todo = np.nonzero(m & np.isnan(mk))[0]
        st["payments"] += int(m.sum())
        st["api"] += int((m & ~np.isnan(mk)).sum())
        if len(todo):
            ko = kline_opens(sym, fms[todo])
            for i in todo:
                mk[i] = ko.get(int(fms[i]), float("nan"))
            st["kl_files_sym"] += 1
        st["kline"] += int((m & np.isnan(fund[sym][2]) & ~np.isnan(mk)).sum())
        st["none"] += int((m & np.isnan(mk)).sum())
        mark[sym] = mk
    return mark, st


def validate_marks(df_all, fund, rng):
    """Сентябрь–ноябрь: open минутки против `mark_price` — разброс замены. Возвращает статистику по выборке платежей, нужных сделкам."""
    pool = {}
    for df in df_all:
        a, b = pay_ranges(df, fund)
        for sym, ix in df.groupby("sym").indices.items():
            if sym in fund:
                fms, rate, mk = fund[sym]
                for i in ix:
                    for j in range(a[i], b[i]):
                        if fms[j] >= SEP_MS and not np.isnan(mk[j]):
                            pool[(sym, j)] = None
    keys = sorted(pool)
    pick = [keys[i] for i in rng.choice(len(keys), size=min(NVAL, len(keys)), replace=False)] if keys else []
    by = {}
    for sym, j in pick:
        by.setdefault(sym, []).append(j)
    rel, got = [], 0
    for sym, js in by.items():
        fms, rate, mk = fund[sym]
        ko = kline_opens(sym, fms[js])
        for j in js:
            o = ko.get(int(fms[j]), float("nan"))
            if o == o:
                rel.append(o / mk[j] - 1)
                got += 1
    rel = np.array(rel)
    return {"sample": len(pick), "with_kline": got, "mean_rel": float(rel.mean()) if len(rel) else float("nan"),
            "abs_mean": float(np.abs(rel).mean()) if len(rel) else float("nan"), "abs_p99": float(np.quantile(np.abs(rel), 0.99)) if len(rel) else float("nan"),
            "abs_max": float(np.abs(rel).max()) if len(rel) else float("nan")}


# ───────────────────────────── капитал ─────────────────────────────

def sweep(t_in, t_out, pnl_exit, t0=D0_MS, t1=END_MS):
    """Развёртка событий. Интервал [вход, выход); выход раньше входа в ту же мс. Возвращает: макс. число позиций, момент максимума, распределение по времени {k: мс},
    число открытых в моменты входов (считая новую), D2 — min депозит с реализованным P&L; сделки нулевой длины запрещены."""
    n = len(t_in)
    assert (t_out > t_in).all(), "сделка нулевой длины"
    t = np.concatenate([t_in, t_out])
    d = np.concatenate([np.ones(n, np.int64), -np.ones(n, np.int64)])
    p = np.concatenate([np.zeros(n), pnl_exit])
    o = np.lexsort((d, t))
    t, d, p = t[o], d[o], p[o]
    c = np.cumsum(d)
    assert c.min() >= 0 and c[-1] == 0
    real = np.cumsum(p)
    need = MARGIN * c - real                               # D(t) = маржа − реализованный P&L после события
    dur = np.diff(np.concatenate([[t0], t, [t1]]))
    assert dur.min() >= 0, "событие вне окна 01.07–13.11"
    cc = np.concatenate([[0], c])
    dist = np.bincount(cc, weights=dur, minlength=int(c.max()) + 1)
    assert abs(dist.sum() - (t1 - t0)) < 1, "длительности не сходятся с окном"
    k = int(np.argmax(c))
    at_entry = c[d == 1]
    return {"max": int(c.max()), "t_max": int(t[k]), "dist_ms": dist, "at_entry": at_entry, "d2": float(max(0.0, need.max())), "mean": float((dist * np.arange(len(dist))).sum() / dist.sum())}


def qtime(dist_ms, q):
    """Наименьшее k, при котором доля времени с числом позиций ≤ k не меньше q."""
    cs = np.cumsum(dist_ms) / dist_ms.sum()
    return int(np.searchsorted(cs, q - 1e-12))


def selftest():
    t_in = np.array([0, 10, 20, 20]) * 1000 + D0_MS
    t_out = np.array([30, 25, 40, 50]) * 1000 + D0_MS
    r = sweep(t_in, t_out, np.array([1.0, 2.0, -4.0, 3.0]), D0_MS, D0_MS + 60_000)
    assert r["max"] == 4 and r["at_entry"].tolist() == [1, 2, 3, 4], r            # к моменту 20: открыты 0, 10 и обе новые
    d = r["dist_ms"]           # 0–10 с одна, 10–20 две, 20–25 четыре, 25–30 три, 30–40 две, 40–50 одна, 50–60 ноль
    assert abs(d[4] - 5000) < 1e-9 and abs(d[3] - 5000) < 1e-9 and abs(d[2] - 20_000) < 1e-9 and abs(d[1] - 20_000) < 1e-9 and abs(d[0] - 10_000) < 1e-9, d.tolist()
    assert abs(d.sum() - 60_000) < 1e-9
    assert abs(r["d2"] - (MARGIN * 4 - 0.0)) < 1e-9, r["d2"]     # реализованный P&L к моменту четырёх позиций = 0 → D2 = 4·250
    # выход и вход в одну мс: выход первым
    r = sweep(np.array([0, 10]) * 1000 + D0_MS, np.array([10, 20]) * 1000 + D0_MS, np.array([0.0, 0.0]), D0_MS, D0_MS + 30_000)
    assert r["max"] == 1, r
    # фандинг: лонг, одна выплата +1 bp, вход раньше, выход позже; шорт получает
    fund = {"X": (np.array([100 * MIN_MS]), np.array([0.0001]), np.array([2.0]))}
    df = pd.DataFrame({"sym": ["X", "X", "X"], "side": ["L", "S", "L"], "t_in": [50 * MIN_MS, 50 * MIN_MS, 100 * MIN_MS], "t_out": [150 * MIN_MS, 150 * MIN_MS, 150 * MIN_MS], "px": [1.0, 1.0, 1.0]})
    tot, rec, paid, cnt, nd = funding_trades(df, fund, {"X": np.array([2.0])}, "mark")
    assert abs(tot[0] - (-0.0001 * 500 * 2.0)) < 1e-12 and abs(tot[1] + tot[0]) < 1e-12 and cnt.tolist() == [1, 1, 0] and tot[2] == 0, (tot, cnt)   # вход ровно на отметке — выплаты нет
    assert abs(paid[0] - tot[0]) < 1e-12 and rec[0] == 0 and abs(rec[1] - tot[1]) < 1e-12 and paid[1] == 0
    tot2, *_ = funding_trades(df, fund, {"X": np.array([2.0])}, "flat")
    assert abs(tot2[0] + 0.05) < 1e-12


# ───────────────────────────── отчёт ─────────────────────────────

def daily(df, nd, extra=None):
    v = df.pnl.to_numpy() + (0.0 if extra is None else extra)
    return np.bincount(df.day.to_numpy(), weights=v, minlength=nd)


def main():
    code, out = sys.argv[1:3]
    os.makedirs(out, exist_ok=True)
    selftest()
    rng = np.random.default_rng(SEED)
    lines, nd = load_lines(code)
    print("сделки загружены, сверка с TK-662: ОК", {k: len(v) for k, v in lines.items()}, flush=True)
    syms = sorted({s for df in lines.values() for s in df.sym})
    fund, cover, overlap = build_funding(syms)
    miss = sorted(set(syms) - set(fund))
    print("монет в сделках %d; фандинг: только zip %d, только api %d, оба %d; без данных %d %s; сентябрь zip↔api: %d общих отметок, %d расхождений ставки" % (
        len(syms), cover["zip"], cover["api"], cover["both"], len(miss), miss[:10], overlap["n"], overlap["bad"]), flush=True)
    df_all = list(lines.values())
    mark, mst = fill_marks(df_all, fund)
    print("марки: %s" % mst, flush=True)
    val = validate_marks(df_all, fund, rng)
    print("проверка свеч-марка (сентябрь–ноябрь): %s" % val, flush=True)

    assert not miss or "--allow-missing" in sys.argv, ("нет фандинга монет — докачать tk664_fetch_missing.py", miss)
    S = {"meta": {"syms": len(syms), "cover": {k: (v if k != "none" else len(v)) for k, v in cover.items()}, "overlap": overlap, "marks": mst, "mark_check": val, "missing_syms": miss}, "lines": {}, "pairs": {}}
    calib = {}
    for name in ORDER:
        df = lines[name].copy()
        tot, rec, paid, cnt, nodata = funding_trades(df, fund, mark, "mark")
        flat = funding_trades(df, fund, mark, "flat")[0]
        f_ja = funding_trades(df, fund, mark, "mark", "ja")[0]
        f_sn = funding_trades(df, fund, mark, "mark", "sn")[0]
        both = (df.eng_fund.to_numpy() != 0) & (f_sn != 0)
        k = float(df.eng_fund.to_numpy()[both].sum() / f_sn[both].sum()) if both.any() else 1.0
        e = df.eng_fund.to_numpy()
        est = np.where(e != 0, e + k * f_ja, k * (f_ja + f_sn))        # сентябрь–ноябрь: поле движка (точные лоты) где оно есть; остальное — модель × k линии
        df["fund_full"], df["fund_rec"], df["fund_paid"], df["fund_n"], df["fund_flat"] = tot, rec, paid, cnt, flat
        df["fund"] = est
        df["fund_ja"], df["fund_sn"] = f_ja, f_sn
        calib[name] = {"k": k, "rows_both": int(both.sum()), "eng_sum_both": float(e[both].sum()), "model_sn_both": float(f_sn[both].sum()), "eng_rows": int((e != 0).sum()),
                       "eng_rows_model_sn_zero": int(((e != 0) & (f_sn == 0)).sum()), "model_sn_rows_eng_zero": int(((e == 0) & (f_sn != 0)).sum()), "model_sn_sum_eng_zero": float(f_sn[(e == 0)].sum()),
                       "model_ja_sum": float(f_ja.sum()), "model_ja_rows": int((f_ja != 0).sum()), "eng_ratio_rows": [round(float(x), 3) for x in (e[both] / f_sn[both])[:60]]}
        df.to_csv(os.path.join(out, "funding-%s.csv" % name), index=False, float_format="%.6f")
        S["lines"][name] = {"calib": calib[name], "funding_full": float(tot.sum())}
        lines[name] = df
    # метрики
    def line_stats(df, tag):
        b, a = daily(df, nd), daily(df, nd, df.fund.to_numpy())
        n = len(df)
        return {"n": n, "sum_before": float(b.sum()), "sum_after": float(a.sum()), "funding": float(df.fund.sum()), "funding_full": float(df.fund_full.sum()), "received": float(df.fund_rec.sum()), "paid": float(df.fund_paid.sum()),
                "funding_flat": float(df.fund_flat.sum()), "mean_before": float(b.sum() / n), "mean_after": float(a.sum() / n), "mean_fund": float(df.fund.sum() / n),
                "sharpe_before": sharpe(b), "sharpe_after": sharpe(a), "payments": int(df.fund_n.sum()), "trades_with_payments": int((df.fund_n > 0).sum()),
                "trades_no_symbol_data": int(df.sym.isin(miss).sum()), "takes_share": float((df.takes > 0).mean()), "eng_fund_sum": float(df.eng_fund.sum()), "eng_fund_nonzero": int((df.eng_fund != 0).sum()),
                "hold_h_mean": float(((df.t_out - df.t_in) / 3_600_000).mean()), "hold_h_max": float(((df.t_out - df.t_in) / 3_600_000).max()),
                "fund_top10_share": float(np.sort(np.abs(df.fund_full.to_numpy()))[::-1][:10].sum() / max(np.abs(df.fund_full.to_numpy()).sum(), 1e-12)),
                "fund_neg_trades": int((df.fund < 0).sum()), "fund_pos_trades": int((df.fund > 0).sum()), "fund_full_sharpe_after": sharpe(daily(df, nd, df.fund_full.to_numpy())), "sum_after_full": float(daily(df, nd, df.fund_full.to_numpy()).sum())}

    def cap_stats(df, tag):
        post = df.pnl.to_numpy() + df.fund.to_numpy()
        r = sweep(df.t_in.to_numpy(), df.t_out.to_numpy(), post)
        pd.DataFrame({"positions": np.arange(len(r["dist_ms"])), "hours": r["dist_ms"] / 3_600_000, "share": r["dist_ms"] / r["dist_ms"].sum()}).to_csv(
            os.path.join(out, "concurrency-%s.csv" % tag), index=False, float_format="%.6f")
        ae = r["at_entry"]
        d1 = MARGIN * r["max"]
        return {"max": r["max"], "t_max": dt.datetime.fromtimestamp(r["t_max"] / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M"), "mean": r["mean"],
                "q95": qtime(r["dist_ms"], 0.95), "q99": qtime(r["dist_ms"], 0.99), "zero_share": float(r["dist_ms"][0] / r["dist_ms"].sum()),
                "max_margin": d1, "mean_margin": MARGIN * r["mean"], "entry_mean": float(ae.mean()), "entry_q95": int(np.quantile(ae, 0.95)), "entry_max": int(ae.max()),
                "d1": d1, "d2": r["d2"], "dist": [float(x) for x in r["dist_ms"] / r["dist_ms"].sum()]}

    for name in ORDER:
        df = lines[name]
        S["lines"][name] = {**S["lines"][name], **line_stats(df, name), **cap_stats(df, name)}
    for pn, (x, y) in PAIRS.items():
        df = pd.concat([lines[x], lines[y]], ignore_index=True)
        S["pairs"][pn] = {**line_stats(df, pn), **cap_stats(df, pn)}
    for blk in (S["lines"], S["pairs"]):
        for k, v in blk.items():
            v["ret_d1_ann"] = v["sum_after"] / v["d1"] * 365 / nd
            v["ret_d1"] = v["sum_after"] / v["d1"]
            v["ret_d1_before"] = v["sum_before"] / v["d1"]
            v["ret_d2"] = v["sum_after"] / v["d2"] if v["d2"] > 0 else float("nan")
    json.dump(S, open(os.path.join(out, "summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, allow_nan=True)
    write_tables(S, out)
    print("готово →", out, flush=True)


def write_tables(S, out):
    pct = t63.pct
    allv = {**S["lines"], **S["pairs"]}
    L = ["## Фандинг: оценка (сентябрь–ноябрь — поле движка, где есть; остальное — модель × k линии) и верхняя оценка (модель, полный лот 500 $ от входа до выхода)", ""]
    rows = []
    for k, v in allv.items():
        rows.append([k, num(v["n"], 0), num(v["sum_before"], 2, True), num(v["funding"], 2, True), num(v["sum_after"], 2, True), num(v["funding_full"], 2, True), num(v["received"], 2, True), num(v["paid"], 2, True),
                     num(v["mean_before"], 3, True), num(v["mean_after"], 3, True), num(v["sharpe_before"], 2), num(v["sharpe_after"], 2), num(v["fund_full_sharpe_after"], 2)])
    L += table(["линия", "сделок", "Σ $ до", "фандинг $ (оценка)", "Σ $ после", "фандинг $ (верхняя)", "получено $ (верхн.)", "уплачено $ (верхн.)", "$/сделку до", "$/сделку после", "Sharpe до", "Sharpe после", "Sharpe после (верхн.)"], rows)
    L += ["## Сверка модели с полем `funding` движка (сентябрь–ноябрь, строки, где у движка фандинг ≠ 0 и у модели выплаты с 01.09 ≠ 0)", ""]
    rows = []
    for k, v in S["lines"].items():
        c = v["calib"]
        rows.append([k, num(c["rows_both"], 0), num(c["eng_sum_both"], 2, True), num(c["model_sn_both"], 2, True), num(c["k"], 3), num(c["eng_rows_model_sn_zero"], 0), num(c["model_sn_rows_eng_zero"], 0), num(c["model_sn_sum_eng_zero"], 2, True),
                     num(c["model_ja_rows"], 0), num(c["model_ja_sum"], 2, True)])
    L += table(["линия", "строк сверки", "движок Σ $", "модель Σ $", "k = движок / модель", "у движка есть, у модели нет", "у модели есть, у движка нет (сент.–нояб.)", "их модель Σ $", "строк с выплатами июль–август", "модель июль–август Σ $"], rows)
    L += ["## Капитал (500 $ на позицию, плечо 2 → маржа 250 $; интервал позиции [вход, выход))", ""]
    rows = []
    for k, v in allv.items():
        rows.append([k, num(v["max"], 0), v["t_max"], num(v["mean"], 2), num(v["q95"], 0), num(v["q99"], 0), num(v["entry_mean"], 1), num(v["entry_q95"], 0), num(v["entry_max"], 0), num(v["d1"], 0), num(v["d2"], 0),
                     pct(v["ret_d1"], 1), pct(v["ret_d1_ann"], 0), pct(v["ret_d2"], 1)])
    L += table(["линия", "макс. позиций", "когда (UTC)", "в среднем", "95 % времени ≤", "99 % ≤", "при входе ср.", "при входе 95 % ≤", "при входе макс.", "D1 $", "D2 $", "Σ после / D1 (136 сут.)", "то же, линейно на год", "Σ после / D2 (136 сут.)"], rows)
    L += ["## Распределение числа одновременных позиций (доля 136 суток)", ""]
    mx = max(len(v["dist"]) for v in allv.values())
    rows = [[k] + [pct(v["dist"][i], 2) if i < len(v["dist"]) else "—" for i in range(mx)] for k, v in allv.items()]
    L += table(["линия"] + [str(i) for i in range(mx)], rows)
    open(os.path.join(out, "tables.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
