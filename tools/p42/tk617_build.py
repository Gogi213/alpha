#!/usr/bin/env python3
"""TK-617: таблица «сделка × признаки» для разбора баз П-42 (описательно, окно 01.07–13.11).

Этапы (каждый пишет файл в --out и пропускается, если файл есть):
  trades  — сделки баз (портфель L_base/S_base) и сигнального режима (L_sig/S_sig) из выходов TK-599, склейка окон как в
            `retitr_full.splice` (зачёт по дате входа), join с `sigsx` потока базы по (монета, уровень, sig_ms): цена уровня, NATR, члены;
  mats    — минутные close и quote_volume 566 монет на сетке 17.06–13.11 (`mat_close.npy`, `mat_qv.npy`, `mat_syms.json`);
  tape    — по ленте aggTrades: путь сделки от входа до выхода и 60 мин (MFE/MAE, момент касания уровня, цена через 1/5/15/60 мин);
  feat    — режим рынка (BTC, средний рынок), монета (оборот, NATR, доходность), фандинг, время суток → `trades_feat.parquet`.
Не читает ничего позже 13.11 (данные окон кончаются 13.11 23:59 UTC).
"""
import argparse
import csv
import datetime
import json
import os
import sys
import zipfile
from multiprocessing import Pool

import numpy as np
import pandas as pd

ROOT = "/data/p42-tk599"
FUND_ZIP = "/data/p42-metrics/funding"
D0 = datetime.date(2026, 7, 1)
DAY_MS = 86_400_000
G0 = datetime.date(2026, 6, 17)                                   # начало минутной сетки (T0 окна wa)
G0_MS = int(datetime.datetime(2026, 6, 17, tzinfo=datetime.timezone.utc).timestamp() * 1000)
NDAYS_GRID = 150                                                  # 17.06 … 13.11
NMIN = NDAYS_GRID * 1440
WINDOWS = (("wa", "2026-06-17", "2026-07-01", "2026-08-25"),
           ("wb", "2026-07-25", "2026-08-26", "2026-10-02"),
           ("wc", "2026-09-01", "2026-10-03", "2026-11-13"))
VARIANTS = {"L_base": ("c53a116125b39", "long", "f686957305744", +1),
            "S_base": ("c10fbba7be522", "short", "fa95205e801e5", -1),
            "L_sig": ("c265612c619e4", "long", "f686957305744", +1),
            "S_sig": ("c07e92626878e", "short", "fa95205e801e5", -1),
            "L_Skeys": ("c10fbba7be522", "long", "fa95205e801e5", +1),     # перекрёст: лонг с ключами шорт-базы (портфель)
            "S_Lkeys": ("c53a116125b39", "short", "f686957305744", -1)}    # перекрёст: шорт с ключами лонг-базы
CHK = (60_000, 300_000, 900_000, 3_600_000)                      # контрольные точки маркаута, мс
H60 = 3_600_000


def t0_ms(t0):
    d = datetime.date.fromisoformat(t0)
    return int(datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc).timestamp() * 1000)


# ---- этап trades ---------------------------------------------------------------------------------------------------------------

def stage_trades(out):
    parts = []
    for name, (vid, side, fid, s) in VARIANTS.items():
        for w, t0, lo, hi in WINDOWS:
            t0d = datetime.date.fromisoformat(t0)
            a, b = (datetime.date.fromisoformat(lo) - D0).days, (datetime.date.fromisoformat(hi) - D0).days
            tr = pd.read_csv("%s/out/%s/%s/%s-trades.csv" % (ROOT, w, vid, side))
            sx = pd.read_csv("%s/sx/%s/%s/%s-sigsx.csv" % (ROOT, w, fid, side))
            sx = sx.drop_duplicates(["sym", "level", "sig_ms"])
            tr["abs_day"] = (t0d - D0).days + tr["entry_ms"] // DAY_MS
            tr = tr[(tr["abs_day"] >= a) & (tr["abs_day"] <= b)].copy()
            n0 = len(tr)
            tr = tr.merge(sx[["sym", "level", "sig_ms", "dist_e8", "level_px_e8", "born", "end", "first_bar", "natr_e8", "members"]],
                          on=["sym", "level", "sig_ms"], how="left")
            miss = int(tr["level_px_e8"].isna().sum())
            if len(tr) != n0:
                raise SystemExit("join размножил строки: %s %s %d → %d" % (name, w, n0, len(tr)))
            tr["set"], tr["win"], tr["s"], tr["miss_sx"] = name, w, s, miss
            tr["t0_ms"] = t0_ms(t0)
            parts.append(tr)
    t = pd.concat(parts, ignore_index=True)
    t["t_in"] = t["t0_ms"] + t["entry_ms"]
    t["t_out"] = t["t0_ms"] + t["exit_ms"]
    t["t_sig"] = t["t0_ms"] + t["sig_ms"]
    t["gf"] = t["gross"] - t["fees"]                              # метрика TK-599: без фандинга
    print(t.groupby("set").agg(n=("gf", "size"), s=("gf", "sum"), miss=("miss_sx", "sum")))
    t.to_parquet(out + "/trades.parquet")


# ---- этап mats -----------------------------------------------------------------------------------------------------------------

def _coin_mat(sym):
    c = np.full(NMIN, np.nan, dtype=np.float32)
    q = np.full(NMIN, np.nan, dtype=np.float32)
    seen = set()
    for w, _, _, _ in WINDOWS:
        d = "%s/%s/klines/%s" % (ROOT, w, sym)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".csv") or fn == "funding.csv" or fn in seen:
                continue
            seen.add(fn)
            df = pd.read_csv(os.path.join(d, fn), usecols=[0, 4, 7], dtype={"open_time": "int64", "close": "float64", "quote_volume": "float64"})
            ix = ((df["open_time"].values - G0_MS) // 60_000).astype(np.int64)
            ok = (ix >= 0) & (ix < NMIN)
            c[ix[ok]] = df["close"].values[ok]
            q[ix[ok]] = df["quote_volume"].values[ok]
    return sym, c, q


def stage_mats(out, procs):
    syms = sorted(os.listdir(ROOT + "/wa/klines"))
    syms = [s for s in syms if os.path.isdir(ROOT + "/wa/klines/" + s)]
    C = np.full((len(syms), NMIN), np.nan, dtype=np.float32)
    Q = np.full((len(syms), NMIN), np.nan, dtype=np.float32)
    ix = {s: i for i, s in enumerate(syms)}
    with Pool(procs) as p:
        for k, (s, c, q) in enumerate(p.imap_unordered(_coin_mat, syms, chunksize=4)):
            C[ix[s]] = c
            Q[ix[s]] = q
            if k % 100 == 0:
                print("mats", k, flush=True)
    np.save(out + "/mat_close.npy", C)
    np.save(out + "/mat_qv.npy", Q)
    json.dump(syms, open(out + "/mat_syms.json", "w"))


# ---- этап tape -----------------------------------------------------------------------------------------------------------------

def _tape_day(sym, day):
    """(времена мс, цены) ленты aggTrades монеты за сутки (UTC) `day` (datetime.date); None, если файла нет."""
    fn = "%s-aggTrades-%s.zip" % (sym, day.isoformat())
    for w, _, _, _ in WINDOWS:
        p = "%s/%s/aggtrades/%s/%s" % (ROOT, w, sym, fn)
        if os.path.exists(p):
            with zipfile.ZipFile(p) as z:
                with z.open(z.namelist()[0]) as f:
                    df = pd.read_csv(f, usecols=["price", "transact_time"], dtype={"price": "float64", "transact_time": "int64"})
            return df["transact_time"].values, df["price"].values
    return None


def _path(ev, t, p):
    """ev — dict события; t, p — лента. Возвращает словарь статистик пути (цены — в бп от цены входа, знак стороны)."""
    s, p0, lp = ev["s"], ev["p0"], ev["lp"]
    r = {}
    i0 = int(np.searchsorted(t, ev["t_in"], "left"))
    ih = int(np.searchsorted(t, ev["t_out"], "right"))
    i6 = int(np.searchsorted(t, ev["t_in"] + H60, "right"))
    ie = max(ih, i6)
    if ie <= i0:
        return r
    jo = min(int(np.searchsorted(t, ev["t_out"], "left")), len(t) - 1)
    r["px_in_tape"], r["t_in_tape"] = float(p[i0]), int(t[i0])    # аудит знаков/цен: цена и время первой сделки ленты ≥ входа
    r["px_out_tape"], r["t_out_tape"] = float(p[jo]), int(t[jo])
    # прокси mk2 (TK-616, §13.28): заявка на уровне живёт до конца минуты сигнала; пробой = сделка за уровнем после T = sig + 4 мс,
    # ретест = сделка строго по эту сторону уровня после пробоя + 4 мс; тик цены не учитывается
    T = ev["t_sig"] + 4
    tend = (T // 60_000 + 1) * 60_000
    a, b = int(np.searchsorted(t, T, "right")), int(np.searchsorted(t, tend, "left"))
    r["pierce_min"], r["retest_min"] = 0, 0
    if b > a:
        yy = s * (p[a:b] - lp) / lp
        h = np.nonzero(yy >= 0)[0]
        if len(h):
            r["pierce_min"] = 1
            k0 = int(np.searchsorted(t, t[a + h[0]] + 4, "right"))
            if k0 < b and (s * (p[k0:b] - lp) / lp < 0).any():
                r["retest_min"] = 1
    x = s * (p[i0:ie] / p0 - 1.0) * 1e4                           # бп в пользу сделки
    y = s * (p[i0:ie] - lp) / lp * 1e4                            # бп за уровнем (>0 — по ту сторону уровня, куда ждём пробой)
    tt = t[i0:ie] - ev["t_in"]
    nh = max(1, ih - i0)                                          # тиков в удержании (≥ 1)
    n6 = max(1, i6 - i0)                                          # тиков в первые 60 мин
    xh, yh = x[:nh], y[:nh]
    x6, y6, t6 = x[:n6], y[:n6], tt[:n6]
    r["mfe_h"], r["mae_h"] = float(xh.max()), float(xh.min())
    r["t_mfe_h"], r["t_mae_h"] = int(tt[int(xh.argmax())]), int(tt[int(xh.argmin())])
    r["mfe_60"], r["mae_60"] = float(x6.max()), float(x6.min())
    r["t_mfe_60"], r["t_mae_60"] = int(t6[int(x6.argmax())]), int(t6[int(x6.argmin())])
    r["exc_lvl_h"], r["exc_lvl_60"] = float(yh.max()), float(y6.max())
    r["dep_lvl_h"], r["dep_lvl_60"] = float(yh.min()), float(y6.min())
    hit = np.nonzero(y6 >= 0)[0]
    r["t_touch"] = int(t6[hit[0]]) if len(hit) else -1             # мс от входа до касания уровня; -1 — не коснулась за 60 мин
    r["touch_h"] = 1 if (yh >= 0).any() else 0
    for c in CHK:
        j = int(np.searchsorted(t6, c, "right")) - 1
        r["mo_%d" % (c // 60_000)] = float(x6[j]) if j >= 0 else np.nan
    if len(hit):                                                   # глубина возврата под уровень после первого касания (бп; <0 — ниже уровня)
        r["dep_after_touch_60"] = float(y6[hit[0]:].min())
    return r


def _tape_sym(args):
    sym, evs = args
    evs = sorted(evs, key=lambda e: e["t_in"])
    cache = {}
    out = []
    for e in evs:
        d0 = (datetime.datetime.fromtimestamp(e["t_in"] / 1000, datetime.timezone.utc)).date()
        d1 = (datetime.datetime.fromtimestamp((max(e["t_out"], e["t_in"] + H60)) / 1000, datetime.timezone.utc)).date()
        days = [d0 + datetime.timedelta(days=k) for k in range((d1 - d0).days + 1)]
        ts, ps = [], []
        for d in days:
            if d not in cache:
                for k in [k for k in cache if k < d - datetime.timedelta(days=2)]:
                    del cache[k]
                cache[d] = _tape_day(sym, d)
            if cache[d] is not None:
                ts.append(cache[d][0])
                ps.append(cache[d][1])
        if not ts:
            out.append((e["key"], {"no_tape": 1}))
            continue
        t, p = (np.concatenate(ts), np.concatenate(ps)) if len(ts) > 1 else (ts[0], ps[0])
        r = _path(e, t, p)
        r["no_tape"] = 0 if r else 1
        out.append((e["key"], r))
    return out


def stage_tape(out, procs):
    t = pd.read_parquet(out + "/trades.parquet")
    t["p0"] = t["entry_px_e8"] / 1e8
    t["lp"] = t["level_px_e8"].abs() / 1e8
    t["key"] = list(zip(t["win"], t["sym"], t["level"], t["sig_ms"], t["s"], t["t_out"]))
    u = t.drop_duplicates("key")
    by = {}
    for r in u.itertuples():
        by.setdefault(r.sym, []).append({"key": r.key, "s": int(r.s), "p0": r.p0, "lp": r.lp, "t_in": int(r.t_in), "t_out": int(r.t_out), "t_sig": int(r.t_sig)})
    jobs = sorted(by.items(), key=lambda kv: -len(kv[1]))
    res = {}
    with Pool(procs) as p:
        for k, lst in enumerate(p.imap_unordered(_tape_sym, jobs, chunksize=1)):
            for key, r in lst:
                res[key] = r
            if k % 25 == 0:
                print("tape", k, len(jobs), flush=True)
    rows = [dict(res.get(k, {"no_tape": 1}), key_idx=i) for i, k in enumerate(t["key"])]
    r = pd.DataFrame(rows).drop(columns=["key_idx"])
    t = pd.concat([t.reset_index(drop=True), r], axis=1).drop(columns=["key"])
    t.to_parquet(out + "/trades_tape.parquet")
    print("no_tape", int(t["no_tape"].sum()), "of", len(t))


# ---- этап feat -----------------------------------------------------------------------------------------------------------------

def load_funding(syms):
    """{монета: (времена мс, ставки)}: фандинг зипы TK-599 (июль–сентябрь) + funding.csv окон (сентябрь–ноябрь)."""
    out = {}
    for s in syms:
        tt, rr = [], []
        for m in ("2026-07", "2026-08", "2026-09"):
            p = "%s/%s/%s-fundingRate-%s.zip" % (FUND_ZIP, s, s, m)
            if os.path.exists(p):
                with zipfile.ZipFile(p) as z:
                    with z.open(z.namelist()[0]) as f:
                        df = pd.read_csv(f, usecols=["calc_time", "last_funding_rate"])
                tt.append(df["calc_time"].values.astype(np.int64))
                rr.append(df["last_funding_rate"].values.astype(float))
        for w, _, _, _ in WINDOWS:
            p = "%s/%s/klines/%s/funding.csv" % (ROOT, w, s)
            if os.path.exists(p):
                df = pd.read_csv(p, usecols=["funding_time", "funding_rate"])
                tt.append(df["funding_time"].values.astype(np.int64))
                rr.append(df["funding_rate"].values.astype(float))
        if tt:
            t, r = np.concatenate(tt), np.concatenate(rr)
            o = np.argsort(t, kind="stable")
            t, r = t[o], r[o]
            keep = np.concatenate([[True], np.diff(t) > 60_000])    # ряды из двух источников: время округлено по-разному
            out[s] = (t[keep], r[keep])
    return out


def stage_feat(out):
    t = pd.read_parquet(out + "/trades_tape.parquet")
    C = np.load(out + "/mat_close.npy", mmap_mode="r")
    Q = np.load(out + "/mat_qv.npy", mmap_mode="r")
    syms = json.load(open(out + "/mat_syms.json"))
    ix = {s: i for i, s in enumerate(syms)}
    btc, eth = ix["BTCUSDT"], ix["ETHUSDT"]
    fund = load_funding(sorted(set(t["sym"])))
    m_sig = ((t["t_sig"].values - G0_MS) // 60_000).astype(np.int64)  # минута, в которой сигнал; последняя закрытая — m-1
    n = len(t)
    cols = {}

    def put(name, arr):
        cols[name] = np.asarray(arr, dtype=float)

    hs = {"1h": 60, "4h": 240, "24h": 1440, "7d": 10080}
    for nm in ("btc", "eth", "mkt", "brd", "coin", "rel"):
        for h in hs:
            cols["%s_r%s" % (nm, h)] = np.full(n, np.nan)
    for nm in ("btc_vol4h", "btc_vol24h", "mkt_vol4h", "btc_day", "mkt_day", "btc_day_vol", "turn24", "turn1h", "turn_rank", "pool_n"):
        cols[nm] = np.full(n, np.nan)
    ci = np.array([ix[s] for s in t["sym"]])
    for k in range(n):
        m = int(m_sig[k])
        if m - 10081 < 0 or m >= NMIN:
            continue
        cp = np.asarray(C[:, m - 1], dtype=float)
        for h_nm, h in hs.items():
            c0 = np.asarray(C[:, m - 1 - h], dtype=float)
            with np.errstate(invalid="ignore", divide="ignore"):
                rr = cp / c0 - 1.0
            cols["btc_r" + h_nm][k] = rr[btc]
            cols["eth_r" + h_nm][k] = rr[eth]
            cols["mkt_r" + h_nm][k] = np.nanmean(rr)
            cols["brd_r" + h_nm][k] = np.nanmean(rr[~np.isnan(rr)] > 0)
            cols["coin_r" + h_nm][k] = rr[ci[k]]
            cols["rel_r" + h_nm][k] = rr[ci[k]] - rr[btc]
        sl = np.asarray(C[:, m - 241:m], dtype=float)
        with np.errstate(invalid="ignore", divide="ignore"):
            lr = np.diff(np.log(sl), axis=1)
        cols["btc_vol4h"][k] = np.nanstd(lr[btc]) * 1e4
        cols["mkt_vol4h"][k] = np.nanmean(np.nanstd(lr, axis=1)) * 1e4
        sl24 = np.asarray(C[btc, m - 1441:m], dtype=float)
        cols["btc_vol24h"][k] = np.nanstd(np.diff(np.log(sl24))) * 1e4
        q24 = np.nansum(np.asarray(Q[:, m - 1440:m], dtype=float), axis=1)
        cols["turn24"][k] = q24[ci[k]]
        cols["turn1h"][k] = np.nansum(np.asarray(Q[ci[k], m - 60:m], dtype=float))
        cols["turn_rank"][k] = float((q24 > q24[ci[k]]).sum()) + 1.0
        d_lo = (m // 1440) * 1440
        d_hi = min(d_lo + 1440, NMIN) - 1
        cd0 = np.asarray(C[:, d_lo - 1], dtype=float)
        cd1 = np.asarray(C[:, d_hi], dtype=float)
        with np.errstate(invalid="ignore", divide="ignore"):
            rd = cd1 / cd0 - 1.0
        cols["btc_day"][k] = rd[btc]
        cols["mkt_day"][k] = np.nanmean(rd)
        cols["btc_day_vol"][k] = np.nanstd(np.diff(np.log(np.asarray(C[btc, d_lo:d_hi + 1], dtype=float)))) * 1e4
    for c, v in cols.items():
        t[c] = v
    # фандинг: последняя ставка до входа
    fr = np.full(n, np.nan)
    for sym, g in t.groupby("sym"):
        if sym not in fund:
            continue
        tf, rf_ = fund[sym]
        j = np.searchsorted(tf, g["t_in"].values, "right") - 1
        v = np.where(j >= 0, rf_[np.clip(j, 0, None)], np.nan)
        fr[g.index.values] = v
    t["fund_rate"] = fr
    dt = pd.to_datetime(t["t_in"], unit="ms", utc=True)
    t["hour_utc"], t["dow"] = dt.dt.hour.values, dt.dt.dayofweek.values
    t["month"] = pd.cut(t["abs_day"], [-1, 30, 61, 91, 122, 140], labels=["jul", "aug", "sep", "oct", "nov"]).astype(str)
    t["natr_bp"] = t["natr_e8"] / 1e8 * 1e4
    t["dist_bp"] = t["dist_e8"] / 1e8 * 1e4
    t["n_members"] = t["members"].fillna("").map(lambda x: len(x.split("|")) if x else 0)
    t["lvl_age_min"] = t["sig_min"] - t["born"]
    t["hold_s"] = (t["t_out"] - t["t_in"]) / 1000.0
    t["mfe_n"] = t["mfe_h"] / 1e4 / (t["natr_bp"] / 1e4)
    t["mae_n"] = t["mae_h"] / 1e4 / (t["natr_bp"] / 1e4)
    t["mfe60_n"] = t["mfe_60"] / t["natr_bp"]
    t["mae60_n"] = t["mae_60"] / t["natr_bp"]
    t.to_parquet(out + "/trades_feat.parquet")
    print("feat rows", len(t), "cols", len(t.columns))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/data/p42-tk617")
    ap.add_argument("--procs", type=int, default=3)
    ap.add_argument("stages", nargs="*", default=["trades", "mats", "tape", "feat"])
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    for st in a.stages:
        print("stage", st, flush=True)
        {"trades": lambda: stage_trades(a.out), "mats": lambda: stage_mats(a.out, a.procs), "tape": lambda: stage_tape(a.out, a.procs),
         "feat": lambda: stage_feat(a.out)}[st]()


if __name__ == "__main__":
    main()
