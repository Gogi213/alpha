"""П-42 TK-602: форма эквити баз U1 — ступени (рывок, плато, рывок) или плавный рост. Только чтение готовых выходов TK-598 и минуток BTCUSDT.

Данные: сделки баз S_u1 / L_u1 (`out/{jul-1,aug-1,F-1}/eng/<вариант>/<сторона>-trades.csv` + `-detail.jsonl`, как в tk601_equity.py), окна «июль + август» (62 суток, 01.07–31.08)
и «окно подбора» (60 суток, 15.09–13.11). Порядок и суммы сделок сверяются с `equity.json` (тем, что показывает вкладка «Эквити»): расхождение — код 1.
Новых моделей и порогов нет. Сутки — календарные UTC по времени закрытия сделки (как кривая эквити). BTC — минутки `/data/p42-tk596/{jul,aug}/klines/BTCUSDT`, `/data/klines/binance/BTCUSDT`.

Использование: <venv>/bin/python tk602_shape.py <каталог /data/p42-tk598> <equity.json> <выходной каталог>
Выход: shape.json (меры, сутки, отмеченные дни, признаки), trades-<сторона>-<окно>.csv (сделки с видом выхода, удержанием, ходом BTC).
Определения:
  рывок / спад — 5 суток с наибольшей / наименьшей суммой P&L (закрытия); плато — серия суток подряд без нового максимума эквити (конец суток, старт 0);
  R² — OLS-прямая по эквити на конец суток; k80 — число суток, дающих 80 % суммы положительных суток;
  ход BTC «за сделку» — закрытие минуты выхода / закрытие минуты перед входом − 1, знак по стороне (+ = в пользу сделки);
  признаки суток, известные до торговли: ход BTC за прошлые 24 ч и 7 сут., разброс часовых доходностей прошлых 24 ч, размах прошлых суток, число сигналов прошлых суток,
  последний фандинг BTC до 00:00 UTC; по сделкам суток (известны на входе): средний NATR и средний последний фандинг монеты; «число сигналов этих суток» — параллельное, заранее не известно.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats

DAY = 86_400_000
MIN = 60_000
SEED = 602
B_PERM = 20_000
K_TOP = 5
SIDES = {"short": "S_u1", "long": "L_u1"}
RUNS = {"ja": [("jul", "jul-1", 0), ("aug", "aug-1", 31)], "fit": [("F", "F-1", 0)]}
FEATURES = [("btc_ret24", "BTC, ход за прошлые 24 ч, %"), ("btc_ret7d", "BTC, ход за прошлые 7 сут., %"), ("btc_vol24", "BTC, разброс часовых доходностей за 24 ч, %"),
            ("btc_range_prev", "BTC, размах прошлых суток, %"), ("sig_prev", "сигналов в прошлые сутки"), ("sig_day", "сигналов в эти сутки (параллельно)"),
            ("fund_btc", "фандинг BTC до начала суток, бп"), ("fund_coins", "фандинг монет сделок на входе, бп"), ("natr", "NATR монет сделок, бп")]


# ───────────────────────────── меры формы (чистые функции) ─────────────────────────────

def runs_stats(d):
    """Серии знаков суток (нулевые сутки пропускаются): число серий, z Вальда–Вольфовица, самая длинная серия плюсов и минусов."""
    s = np.sign(np.asarray(d, float))
    s = s[s != 0]
    n1, n2 = int((s > 0).sum()), int((s < 0).sum())
    if len(s) == 0:
        return {"n_runs": 0, "z": None, "longest_pos": 0, "longest_neg": 0, "n_pos": 0, "n_neg": 0}
    cuts = np.flatnonzero(s[1:] != s[:-1]) + 1
    bounds = np.concatenate([[0], cuts, [len(s)]])
    lens, sg = np.diff(bounds), s[bounds[:-1]]
    n_runs = len(lens)
    z = None
    if n1 and n2:
        mu = 2 * n1 * n2 / (n1 + n2) + 1
        var = (mu - 1) * (mu - 2) / (n1 + n2 - 1)
        z = (n_runs - mu) / np.sqrt(var) if var > 0 else None
    return {"n_runs": int(n_runs), "z": None if z is None else float(z), "longest_pos": int(lens[sg > 0].max()) if n1 else 0,
            "longest_neg": int(lens[sg < 0].max()) if n2 else 0, "n_pos": n1, "n_neg": n2}


def plateau_stats(e):
    """Плато по эквити на конец суток (старт 0): сутки без нового максимума, самая длинная серия таких суток; макс. просадка $ и её длина в сутках (пик → возврат к пику или конец)."""
    e = np.asarray(e, float)
    peak = np.maximum.accumulate(np.concatenate([[0.0], e]))[:-1]       # максимум до этих суток
    new_high = e > peak
    longest = cur = 0
    for v in new_high:
        cur = 0 if v else cur + 1
        longest = max(longest, cur)
    run_peak = np.maximum.accumulate(np.concatenate([[0.0], e]))[1:]
    dd = run_peak - e
    j = int(dd.argmax()) if len(dd) else 0
    maxdd = float(dd[j]) if len(dd) else 0.0
    dd_days = 0
    if maxdd > 0:
        pk_val = run_peak[j]
        idx_pk = np.flatnonzero(e[:j + 1] == pk_val)
        d_pk = int(idx_pk[-1]) if len(idx_pk) else -1          # сутки пика (−1 — старт 0 до первых суток)
        rec = np.flatnonzero(e[j:] >= pk_val)
        d_rec = j + int(rec[0]) if len(rec) else len(e)
        dd_days = d_rec - d_pk
    return {"nonew_days": int((~new_high).sum()), "nonew_pct": float((~new_high).mean()), "longest_plateau": int(longest), "maxdd": maxdd, "maxdd_days": int(dd_days)}


def r2_line(e):
    """R² OLS-прямой по эквити на конец суток."""
    e = np.asarray(e, float)
    x = np.arange(len(e), dtype=float)
    if len(e) < 3 or e.std() == 0:
        return float("nan"), float("nan")
    sl, ic = np.polyfit(x, e, 1)
    ss_res = ((e - (sl * x + ic)) ** 2).sum()
    ss_tot = ((e - e.mean()) ** 2).sum()
    return float(1 - ss_res / ss_tot), float(sl)


def top_share(vals, k, total=None):
    """Сумма k наибольших значений и их доля суммы (None, если сумма ≤ 0) и доля суммы положительных значений."""
    v = np.sort(np.asarray(vals, float))[::-1]
    s = float(v[:k].sum())
    tot = float(np.sum(vals)) if total is None else total
    pos = float(v[v > 0].sum())
    return s, (s / tot if tot > 0 else None), (min(s, pos) / pos if pos > 0 else None)


def k80(d):
    pos = np.sort(np.asarray(d, float)[np.asarray(d) > 0])[::-1]
    if len(pos) == 0:
        return 0
    c = np.cumsum(pos)
    return int(np.searchsorted(c, 0.8 * c[-1]) + 1)


def measures(net, dc, n_days, rng, with_null=True):
    """Все меры формы по сделкам (net — P&L, dc — индекс суток закрытия) и числу суток окна."""
    net = np.asarray(net, float)
    d = np.bincount(dc, weights=net, minlength=n_days)[:n_days] if len(net) else np.zeros(n_days)
    cnt = np.bincount(dc, minlength=n_days)[:n_days] if len(net) else np.zeros(n_days, int)
    e = np.cumsum(d)
    total = float(net.sum())
    m = {"n": int(len(net)), "n_days": int(n_days), "total": total, "mean_trade": float(net.mean()) if len(net) else None, "days_no_trades": int((cnt == 0).sum())}
    for k in (1, 3, 5):
        s, sh, shp = top_share(d, k, total)
        m[f"top{k}d_sum"], m[f"top{k}d_share"], m[f"top{k}d_pos_share"] = s, sh, shp
    for k in (5, 10):
        s, sh, shp = top_share(net, k, total)
        m[f"top{k}t_sum"], m[f"top{k}t_share"], m[f"top{k}t_pos_share"] = s, sh, shp
    m["k80"] = k80(d)
    m["k80_pct"] = m["k80"] / n_days
    m.update(plateau_stats(e))
    m["r2"], m["slope_day"] = r2_line(e)
    m["day_mean"], m["day_sd"] = float(d.mean()), float(d.std(ddof=1))
    m["day_skew"] = float(stats.skew(d)) if d.std() > 0 else None
    m["day_kurt"] = float(stats.kurtosis(d)) if d.std() > 0 else None      # избыточный
    m["trade_skew"] = float(stats.skew(net)) if len(net) > 2 and net.std() > 0 else None
    m["trade_kurt"] = float(stats.kurtosis(net)) if len(net) > 3 and net.std() > 0 else None
    m["days_pos"], m["days_neg"] = int((d > 0).sum()), int((d < 0).sum())
    m["best_day"], m["worst_day"] = float(d.max()), float(d.min())
    m["runs"] = runs_stats(d)
    if with_null:
        # (а) перестановки суток: порядок суток — случайный, значения те же (R², плато; перцентиль наблюдённого среди перестановок)
        r2s, pls = np.empty(B_PERM), np.empty(B_PERM)
        for b in range(B_PERM):
            q = rng.permutation(d)
            r2s[b] = r2_line(np.cumsum(q))[0]
            pls[b] = plateau_stats(np.cumsum(q))["longest_plateau"]
        m["null_perm"] = {"r2_pctile": float((r2s < m["r2"]).mean() * 100) if m["r2"] == m["r2"] else None, "r2_q": [float(x) for x in np.nanpercentile(r2s, [5, 50, 95])],
                          "plateau_pctile": float((pls < m["longest_plateau"]).mean() * 100), "plateau_q": [float(x) for x in np.percentile(pls, [5, 50, 95])]}
        # (б) iid-нормальные сутки с тем же средним и σ: доля 5 лучших суток в сумме положительных (модель на допущении iid-нормальности)
        sd = m["day_sd"]
        sim = rng.normal(m["day_mean"], sd if sd > 0 else 1e-9, size=(B_PERM, n_days))
        srt = -np.sort(-sim, axis=1)
        pos = np.where(sim > 0, sim, 0).sum(axis=1)
        t5 = np.minimum(srt[:, :5].sum(axis=1), pos)
        sh = np.where(pos > 0, t5 / np.where(pos > 0, pos, 1), np.nan)
        m["null_normal"] = {"top5_pos_share_q": [float(x) for x in np.nanpercentile(sh, [5, 50, 95])]}
    return m, d, cnt


# ───────────────────────────── загрузка ─────────────────────────────

def load_btc(base_dirs, g0_ms):
    """Минутки BTCUSDT: open/high/low/close на сетке от g0 (по open_time); пропуски — NaN, затем протяжка вперёд."""
    frames = []
    for p in base_dirs:
        for fn in sorted(os.listdir(p)):
            if len(fn) == 14 and fn.endswith(".csv"):
                frames.append(pd.read_csv(os.path.join(p, fn), usecols=["open_time", "high", "low", "close"]))
    k = pd.concat(frames).drop_duplicates("open_time").sort_values("open_time")
    idx = ((k["open_time"].to_numpy(np.int64) - g0_ms) // MIN).astype(np.int64)
    n = int(idx.max()) + 1
    out = {c: np.full(n, np.nan) for c in ("high", "low", "close")}
    for c in out:
        out[c][idx] = k[c].to_numpy(float)
    miss = int(np.isnan(out["close"]).sum())
    for c in out:
        s = pd.Series(out[c]).ffill().to_numpy()
        out[c] = s
    return out, miss


def btc_features(btc, t0_ms, g0_ms):
    """Признаки BTC на начало суток t0 (последние данные — до 00:00) и сам ход суток."""
    i = int((t0_ms - g0_ms) // MIN)                      # первая минута суток
    c, hi, lo = btc["close"], btc["high"], btc["low"]
    pc = c[i - 1]
    f = {"btc_ret24": (pc / c[i - 1 - 1440] - 1) * 100, "btc_ret7d": (pc / c[i - 1 - 7 * 1440] - 1) * 100}
    hc = c[i - 1440:i].reshape(24, 60)[:, -1]
    hr = np.diff(np.log(np.concatenate([[c[i - 1440 - 1]], hc])))
    f["btc_vol24"] = float(hr.std(ddof=1) * 100)
    f["btc_range_prev"] = float((hi[i - 1440:i].max() - lo[i - 1440:i].min()) / c[i - 1440 - 1] * 100)
    # сами сутки
    cd = c[i:i + 1440]
    hcl = cd.reshape(24, 60)[:, -1]
    hr_d = np.diff(np.concatenate([[pc], hcl])) / np.concatenate([[pc], hcl[:-1]]) * 100
    j = int(np.abs(hr_d).argmax())
    day = {"btc_day": float((cd[-1] / pc - 1) * 100), "btc_day_range": float((hi[i:i + 1440].max() - lo[i:i + 1440].min()) / pc * 100), "btc_hour_max": float(hr_d[j]), "btc_hour_max_h": j}
    return f, day


def window_trades(base, run, side, win, day_off, o_ms):
    import tk598_lib as L
    d = os.path.join(base, "out", run, "eng", SIDES[side])
    tr = L.add_labels(L.trade_frame(os.path.join(d, f"{side}-trades.csv"), os.path.join(d, f"{side}-detail.jsonl")))
    tr = tr[L.window_mask(tr["day"], win)].reset_index(drop=True)
    tr["t"] = o_ms + tr["exit_ms"].astype("int64")
    tr["en"] = o_ms + tr["entry_ms"].astype("int64")
    tr["sig_abs"] = o_ms + tr["sig_ms"].astype("int64")
    tr["sig_day"] = (tr["day"].astype("int64") - L.WINDOWS[win][0] + day_off).astype(int)
    tr["w"] = win
    sg = pd.read_csv(os.path.join(d, f"{side}-sigsx.csv"), usecols=["sig_ms"])
    sdays = (sg["sig_ms"].astype("int64") // DAY - L.WINDOWS[win][0] + day_off).to_numpy()
    sdays = sdays[(sdays >= day_off) & (sdays < day_off + L.WINDOWS[win][2])]
    return tr, sdays


def t0_ms(win_t0):
    return int(dt.datetime.strptime(win_t0, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def clean(o):
    """NaN → None, numpy → python (JSON без NaN)."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if o != o or o in (float("inf"), float("-inf")) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def spearman(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = ~(np.isnan(x) | np.isnan(y))
    if ok.sum() < 6 or np.nanstd(x[ok]) == 0 or np.nanstd(y[ok]) == 0:
        return None
    r, p = stats.spearmanr(x[ok], y[ok])
    return {"rho": float(r), "p": float(p), "n": int(ok.sum())}


def auc_perm(x, grp, rng, b=10_000):
    """AUC признака для группы (grp=True) против остальных и двусторонняя перестановочная p."""
    x, grp = np.asarray(x, float), np.asarray(grp, bool)
    ok = ~np.isnan(x)
    x, grp = x[ok], grp[ok]
    n1, n2 = int(grp.sum()), int((~grp).sum())
    if n1 < 3 or n2 < 3 or np.ptp(x) == 0:
        return None
    r = stats.rankdata(x)

    def auc(g):
        return (r[g].sum() - n1 * (n1 + 1) / 2) / (n1 * n2)
    a = auc(grp)
    sims = np.empty(b)
    for i in range(b):
        g = np.zeros(len(x), bool)
        g[rng.choice(len(x), n1, replace=False)] = True
        sims[i] = auc(g)
    p = float((np.abs(sims - 0.5) >= abs(a - 0.5) - 1e-12).mean())
    return {"auc": float(a), "p": p, "n1": n1, "n2": n2, "med_grp": float(np.median(x[grp])), "med_rest": float(np.median(x[~grp]))}


def main():
    base, eq_path, out_dir = sys.argv[1:4]
    sys.path.insert(0, os.path.join(base, "py"))
    import tk594_feat as FEAT
    import tk598_lib as L
    os.makedirs(out_dir, exist_ok=True)
    EQ = json.load(open(eq_path, encoding="utf-8"))
    assert not EQ["recon"]["bad"], "equity.json: расхождения сверки"
    rng = np.random.default_rng(SEED)
    g0 = t0_ms("2026-06-17")
    btc, miss = load_btc(["/data/p42-tk596/jul/klines/BTCUSDT", "/data/p42-tk596/aug/klines/BTCUSDT", "/data/klines/binance/BTCUSDT"], g0)
    fund_cache = {}

    def fund_at(sym, t):
        if sym not in fund_cache:
            fund_cache[sym] = FEAT.load_funding(sym)
        ts, fr = fund_cache[sym]
        j = np.searchsorted(ts, t, side="right") - 1
        return float(fr[j]) * 1e4 if j >= 0 else np.nan
    btc_ts, btc_fr = FEAT.load_funding("BTCUSDT")
    doc = {"meta": {"note": "сутки — UTC по закрытию сделки; net = gross − fees без фандинга (как вкладка «Эквити»)", "btc_missing_minutes_filled": miss, "k_top": K_TOP, "perm": B_PERM, "seed": SEED,
                    "tests_note": "AUC и ρ — на малых выборках; 4 окна × 9 признаков × 4 меры = 144 сравнения, по случайности ≈ 7 дадут p < 0,05"},
           "windows": {}, "sides": {}}
    recon = []
    for side in SIDES:
        doc["sides"][side] = {}
        for wid, runs in RUNS.items():
            C = EQ["charts"][wid]
            start = int(C["start_ms"])
            parts, sig_days = [], []
            for win, run, off in runs:
                tr, sd_ = window_trades(base, run, side, win, off, t0_ms(L.WINDOWS[win][3]))
                parts.append(tr)
                sig_days.append(sd_)
            tr = pd.concat(parts, ignore_index=True)
            order = np.lexsort((tr["sym"].to_numpy(), tr["en"].to_numpy(), tr["t"].to_numpy()))
            tr = tr.iloc[order].reset_index(drop=True)
            S = C["sides"][side]
            ok = (tr["t"].tolist() == S["t"] and tr["en"].tolist() == S["en"] and tr["sym"].tolist() == S["sym"] and np.allclose(tr["net_nf"].to_numpy(), S["net"], atol=1e-9))
            recon.append({"side": side, "window": wid, "n": len(tr), "ok": bool(ok), "sum": float(tr["net_nf"].sum())})
            if not ok:
                continue
            n_days = sum(L.WINDOWS[w][2] for w, _, _ in runs)
            tr["dc"] = ((tr["t"] - start) // DAY).astype(int)
            n_days = max(n_days, int(tr["dc"].max()) + 1)
            tr["hold_min"] = (tr["t"] - tr["en"]) / MIN
            # ход BTC за сделку, знак по стороне
            sgn = -1.0 if side == "short" else 1.0
            ie, ix = ((tr["en"] - g0) // MIN).to_numpy() - 1, ((tr["t"] - g0) // MIN).to_numpy()
            tr["btc_aligned_pct"] = sgn * (btc["close"][ix] / btc["close"][ie] - 1) * 100
            tr["fund_bp"] = [fund_at(s, int(e)) for s, e in zip(tr["sym"], tr["en"])]
            tr["natr_bp"] = tr["natr_e8"] / 1e4
            m_base, d, cnt = measures(tr["net_nf"].to_numpy(), tr["dc"].to_numpy(), n_days, rng)
            e_day = np.cumsum(d)
            up = [int(i) for i in np.argsort(-d, kind="stable")[:K_TOP]]
            dn = [int(i) for i in np.argsort(d, kind="stable")[:K_TOP]]
            # суточные признаки
            feats, dayinfo = {k: [] for k, _ in FEATURES}, []
            sig_cnt = np.bincount(np.concatenate(sig_days), minlength=n_days)[:n_days] if len(np.concatenate(sig_days)) else np.zeros(n_days, int)
            for di in range(n_days):
                f, dd_ = btc_features(btc, start + di * DAY, g0)
                sub = tr[tr["dc"] == di]
                row = {**f, "sig_prev": float(sig_cnt[di - 1]) if di > 0 else np.nan, "sig_day": float(sig_cnt[di]), "natr": float(sub["natr_bp"].mean()) if len(sub) else np.nan,
                       "fund_coins": float(np.nanmean(sub["fund_bp"])) if len(sub) and not sub["fund_bp"].isna().all() else np.nan}
                j = np.searchsorted(btc_ts, start + di * DAY, side="right") - 1
                row["fund_btc"] = float(btc_fr[j]) * 1e4 if j >= 0 else np.nan
                for k, _ in FEATURES:
                    feats[k].append(row[k])
                dayinfo.append(dd_)
            # сутки: подробности
            def day_detail(di):
                sub = tr[tr["dc"] == di].sort_values("t")
                if not len(sub):
                    return {"n": 0}
                byc = sub.groupby("sym")["net_nf"].sum()
                top_coin = byc.abs().idxmax()
                tt = sub["t"].to_numpy()
                best_w, best_sum = 0, 0.0
                for a in range(len(tt)):
                    wmask = (tt >= tt[a]) & (tt < tt[a] + 3600e3)
                    if wmask.sum() > best_w:
                        best_w, best_sum = int(wmask.sum()), float(sub["net_nf"].to_numpy()[wmask].sum())
                return {"n": int(len(sub)), "coins": int(sub["sym"].nunique()), "pnl": float(sub["net_nf"].sum()), "n_win": int((sub["net_nf"] > 0).sum()), "top_coin": str(top_coin),
                        "top_coin_pnl": float(byc[top_coin]), "kinds": {k: int(v) for k, v in sub["kind"].value_counts().items()}, "hold_med": float(sub["hold_min"].median()),
                        "hold_max": float(sub["hold_min"].max()), "close_h": [int(h) for h in sorted(((sub["t"] - start) // 3600000 % 24).tolist())], "cluster60_n": best_w, "cluster60_pnl": best_sum,
                        "btc_aligned_mean": float(sub["btc_aligned_pct"].mean()), "btc_aligned_pos_share": float((sub["btc_aligned_pct"] > 0).mean()),
                        "best_trade": float(sub["net_nf"].max()), "worst_trade": float(sub["net_nf"].min())}
            marked = {}
            for di in sorted(set(up + dn)):
                marked[str(di)] = {**day_detail(di), **dayinfo[di], "date_ms": int(start + di * DAY)}
            # меры по линиям: база и выбранные «трети» (позиции sel — те же, что в equity.json)
            lines = {"base": m_base}
            for t_name, idx in S["sel"].items():
                msk = np.zeros(len(tr), bool)
                msk[idx] = True
                lines[t_name] = measures(tr["net_nf"].to_numpy()[msk], tr["dc"].to_numpy()[msk], n_days, rng)[0]
            # доля P&L по 4-часовым окнам закрытия UTC
            hb = ((tr["t"] - start) // 3600000 % 24 // 4).astype(int)
            by_h = [float(tr["net_nf"][hb == k].sum()) for k in range(6)]
            by_h_n = [int((hb == k).sum()) for k in range(6)]
            # рыночный или одиночный: Spearman P&L сделки (бп) и хода BTC за сделку
            rho_btc = spearman(tr["bps"], tr["btc_aligned_pct"])
            # признаки: AUC по группам и Spearman по активным суткам
            act = cnt > 0
            fa = {}
            for k, _ in FEATURES:
                x = np.asarray(feats[k], float)
                fa[k] = {"rho_pnl": spearman(x[act], d[act]), "rho_abs": spearman(x[act], np.abs(d[act])), "jump": auc_perm(x[act], np.isin(np.arange(n_days), up)[act], rng),
                         "drop": auc_perm(x[act], np.isin(np.arange(n_days), dn)[act], rng)}
            top_t = tr.sort_values("net_nf", ascending=False)
            keep = ["sym", "level", "kind", "takes", "net_nf", "bps", "nominal", "en", "t", "hold_min", "btc_aligned_pct", "natr_bp", "fund_bp", "dc"]
            ttab = lambda df: [{k: (None if (isinstance(v, float) and v != v) else (v.item() if hasattr(v, "item") else v)) for k, v in r.items()} for r in df[keep].to_dict("records")]
            tr[keep].to_csv(os.path.join(out_dir, f"trades-{side}-{wid}.csv"), index=False)
            doc["sides"][side][wid] = {"start_ms": start, "n_days": n_days, "daily": [float(v) for v in d], "trades_day": [int(v) for v in cnt], "eq_day": [float(v) for v in e_day],
                                       "coins_day": [int(tr[tr["dc"] == i]["sym"].nunique()) for i in range(n_days)], "btc_day": [x["btc_day"] for x in dayinfo],
                                       "btc_range": [x["btc_day_range"] for x in dayinfo], "jump": up, "drop": dn, "marked": marked, "measures": lines, "by_4h": {"pnl": by_h, "n": by_h_n},
                                       "rho_btc_trade": rho_btc, "features": {k: [None if v != v else float(v) for v in vals] for k, vals in feats.items()}, "feat_tests": fa,
                                       "top_trades": ttab(top_t.head(10)), "bottom_trades": ttab(top_t.tail(10).iloc[::-1]),
                                       "kinds": {k: {"n": int(v), "sum": float(tr[tr["kind"] == k]["net_nf"].sum())} for k, v in tr["kind"].value_counts().items()}}
            print(side, wid, "сделок", len(tr), "сумма", round(float(tr["net_nf"].sum()), 2), "суток", n_days, flush=True)
    doc["recon"] = recon
    doc["features_list"] = FEATURES
    json.dump(clean(doc), open(os.path.join(out_dir, "shape.json"), "w", encoding="utf-8"), ensure_ascii=False, allow_nan=False)
    bad = [r for r in recon if not r["ok"]]
    print("сверка с equity.json:", "OK" if not bad else f"РАСХОЖДЕНИЯ {bad}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
