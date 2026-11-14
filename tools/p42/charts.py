#!/usr/bin/env python3
"""П-42: график каждой сделки базы b0 (тики 4 мс, §13.4 п. 8) + признаки сделок + HTML-индекс. Запуск на srv.

Вход: `breakout tick` бинаря v12 с `P42_DETAIL=1` → `<detail>/{long,short}-detail.jsonl` (ноги, уровень на сигнале с пивотами,
NATR выхода). Минутки — `/data/klines/binance/<МОНЕТА>/<день>.csv`, сделки — `/data/aggtrades/binance/...zip`.

  python3 charts.py features <detail_dir> <work_dir> [--only id,id] [--workers 2]   # features.jsonl/csv + кэш тиков
  python3 charts.py render   <detail_dir> <work_dir> [--only id,id] [--out DIR]     # PNG на сделку
  python3 charts.py index    <work_dir> <pub_dir>                                   # index.html + features.csv/jsonl
"""
import argparse
import csv
import json
import math
import os
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

T0 = 1_788_220_800_000  # 2026-09-01 00:00 UTC, мс
MIN, HOUR, DAY = 60_000, 3_600_000, 86_400_000
N_DAYS = 74
START_DAY = 14
GMT4 = 4 * HOUR
KL = "/data/klines/binance"
AGG = "/data/aggtrades/binance"
NOTIONAL = 500.0
MAKER_BPS = 1.4 * 0.9
TAKER_BPS = 3.5 * 0.9
# параметры базы b0 (лежат в meta.json варианта; здесь — подписи графика)
CFG = dict(tf_min=60, tau=0.002, e=0.003, l_days=7, s=0.05)

KIND_RU = {"stop": "стоп", "be": "безубыток", "trail": "трейл", "forced": "принудительное закрытие"}
KIND_SHORT = {"stop": "стоп", "be": "безубыток", "trail": "трейл", "forced": "принуд."}
SIDE_RU = {"long": "лонг", "short": "шорт"}

UP, DOWN = "#b3c1d3", "#5a626d"
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
C_TAKE, C_BE, C_TRAIL, C_STOP, C_FORCED = "#008300", "#6250d6", "#1baf7a", "#d03b3b", "#6b6a66"
KIND_COLOR = {"stop": C_STOP, "be": C_BE, "trail": C_TRAIL, "forced": C_FORCED}
KIND_MARK = {"stop": "X", "be": "s", "trail": "D", "forced": "P"}


def fmt_ts(ms, tz=GMT4, secs=True, ms_=False):
    d = datetime.fromtimestamp((ms + tz) / 1000, tz=timezone.utc)
    s = d.strftime("%d.%m %H:%M:%S" if secs else "%d.%m %H:%M")
    return s + (".%03d" % (ms % 1000) if ms_ else "")


def fmt_px(v):
    return f"{v:.6g}"


def fmt_dur(s):
    s = int(round(s))
    if s < 60:
        return f"{s} с"
    if s < 3600:
        return f"{s // 60} мин {s % 60:02d} с"
    h, r = divmod(s, 3600)
    if h < 48:
        return f"{h} ч {r // 60:02d} мин"
    return f"{h // 24} сут. {h % 24} ч"


# ───────────────────────────── данные ─────────────────────────────


def load_trades(detail_dir, only=None):
    out = []
    for side in ("long", "short"):
        f = os.path.join(detail_dir, "b0_lx4", f"{side}-detail.jsonl")
        for line in open(f, encoding="utf-8"):
            r = json.loads(line)
            r["id"] = f"{side[0].upper()}{r['i'] + 1:04d}_{r['sym']}"
            for k in ("entry_ms", "exit_ms", "sig_ms"):  # движок пишет мс от 01.09 00:00 UTC → абсолютные
                r[k] += T0
            for g in r["legs"]:
                g[0] += T0
            out.append(r)
    if only:
        out = [r for r in out if r["id"] in only or r["id"].split("_")[0] in only]
    return out


_kl_cache = {}


def klines(sym):
    """Минутки монеты на сетке с 01.09: (5, N_MIN) o,h,l,c,quote_volume; дыры — NaN."""
    if sym in _kl_cache:
        return _kl_cache[sym]
    _kl_cache.clear()
    a = np.full((5, N_DAYS * 1440), np.nan)
    d0 = date(2026, 9, 1)
    for d in range(N_DAYS):
        f = f"{KL}/{sym}/{(d0 + timedelta(d)):%Y-%m-%d}.csv"
        if not os.path.exists(f):
            continue
        df = pd.read_csv(f, usecols=["open_time", "open", "high", "low", "close", "quote_volume"])
        idx = ((df["open_time"].values - T0) // MIN).astype(int)
        ok = (idx >= 0) & (idx < a.shape[1])
        for k, c in enumerate(["open", "high", "low", "close", "quote_volume"]):
            a[k, idx[ok]] = df[c].values[ok]
    _kl_cache[sym] = a
    return a


def agg_bars(a, m0, m1, tf):
    """Свечи tf минут из минуток [m0, m1): m0 выравнивается вниз по tf. → (t_ms_start, o, h, l, c)."""
    m0 -= m0 % tf
    m1 = m1 + (-m1) % tf
    m0, m1 = max(m0, 0), min(m1, a.shape[1])
    n = (m1 - m0) // tf
    seg = a[:, m0 : m0 + n * tf].reshape(5, n, tf)
    with np.errstate(all="ignore"):
        o = np.array([x[~np.isnan(x)][0] if (~np.isnan(x)).any() else np.nan for x in seg[0]])
        c = np.array([x[~np.isnan(x)][-1] if (~np.isnan(x)).any() else np.nan for x in seg[3]])
        h = np.nanmax(seg[1], axis=1)
        l = np.nanmin(seg[2], axis=1)
    t = T0 + (m0 + np.arange(n) * tf) * MIN
    return t, o, h, l, c


def extract_ticks(sym, lo_ms, hi_ms):
    """Сделки монеты за [lo_ms, hi_ms] (абс. мс): awk по распакованным суткам → (ms, px, bm), по времени."""
    rows = []
    for d in range((lo_ms - T0) // DAY, (hi_ms - T0) // DAY + 1):
        day = (date(2026, 9, 1) + timedelta(d)).isoformat()
        z = f"{AGG}/{sym}/{sym}-aggTrades-{day}.zip"
        if not os.path.exists(z):
            continue
        prog = (
            "NR>1 && $6>=%d && $6<=%d {print $6\",\"$2\",\"$7} NR>1 && $6>%d {exit}" % (lo_ms, hi_ms, hi_ms + HOUR)
        )
        cmd = f"unzip -p {z} | awk -F, '{prog}'"
        out = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True).stdout
        for ln in out.splitlines():
            t, p, b = ln.split(",")
            rows.append((int(t), float(p), b.startswith("t")))
    rows.sort(key=lambda x: x[0])
    if not rows:
        return np.zeros(0, np.int64), np.zeros(0), np.zeros(0, bool)
    return (
        np.array([r[0] for r in rows], dtype=np.int64),
        np.array([r[1] for r in rows]),
        np.array([r[2] for r in rows], dtype=bool),
    )


def tick_path(work, tid):
    return os.path.join(work, "ticks", tid + ".npz")


def load_ticks(work, tid):
    z = np.load(tick_path(work, tid))
    return z["t"], z["p"], z["b"]


# ───────────────────────────── ноги и признаки ─────────────────────────────


def legs_of(r):
    """Ноги с деньгами: [{n, kind, ms, px, u_usd, gross, fee, net, bps}]; entry — отдельно."""
    s = 1 if r["side"] == "long" else -1
    e = r["entry_px_e8"] / 1e8
    out = []
    tk = 0
    for k, (ms, px, u, maker) in enumerate(r["legs"]):
        p = px / 1e8
        usd = u / 1e6
        g = usd * s * (p - e) / e
        fee = usd * (p / e) * (MAKER_BPS if maker else TAKER_BPS) / 1e4
        last = k == len(r["legs"]) - 1
        if last:
            kind, label = r["kind"], KIND_SHORT[r["kind"]]
        else:
            tk += 1
            kind, label = "take", f"тейк {tk}"
        out.append(dict(n=k, kind=kind, label=label, ms=ms, px=p, usd=usd, gross=g, fee=fee, net=g - fee,
                        bps=s * (p - e) / e * 1e4, maker=bool(maker)))
    return out


def features(r, tk, a):
    """Признаки сделки: ticks `tk` = (ms, px, bm) за [вход−120 с, вход+30 мин]; a — минутки монеты."""
    s = 1 if r["side"] == "long" else -1
    L = r["level_px_e8"] / 1e8
    e = r["entry_px_e8"] / 1e8
    em, xm, sm = r["entry_ms"], r["exit_ms"], r["sig_ms"]
    t, p, bm = tk
    f = dict(id=r["id"], side=r["side"], sym=r["sym"], i=r["i"])
    f["entry_utc"] = datetime.fromtimestamp((em) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    f["entry_gmt4"] = datetime.fromtimestamp((em + GMT4) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    f["day"] = int((em - T0) // DAY - START_DAY)
    f["half"] = "A" if f["day"] < 30 else "B"
    f["level_px"] = L
    f["entry_px"] = e
    f["sig_px"] = L * (1 - s * r["dist_e8"] / 1e8)
    f["dist_sig_bps"] = r["dist_e8"] / 1e8 * 1e4
    f["dist_entry_bps"] = s * (L - e) / L * 1e4  # + : вход по эту сторону уровня (до пробоя), − : уже за уровнем
    f["entry_beyond_level"] = bool(s * e >= s * L)
    f["level_age_min"] = int(r["sig_min"] - r["born"])
    f["level_age_first_pivot_h"] = round((em - T0 - r["first_bar"] * HOUR) / HOUR, 2)
    f["n_pivots"] = len(r["members"])
    pv = [m[1] / 1e8 for m in r["members"]]
    f["pivot_spread_bps"] = (max(pv) - min(pv)) / (sum(pv) / len(pv)) * 1e4
    f["natr_pct"] = r["natr_e8"] / 1e8 * 100
    tch, first_m, me_ = find_touches(r, a)
    f["touches"] = len(tch)
    f["touches_pivot"] = len(r["members"])
    f["touches_other"] = len(tch) - len(r["members"])
    f["touches_pierce"] = sum(1 for d in tch if d["over"] > CFG["tau"])
    f["touches_last_60m"] = sum(1 for d in tch if d["m"] >= me_ - 60)
    f["last_touch_age_min"] = int(me_ - tch[-1]["m"])
    f["first_touch_age_h"] = round((me_ - first_m) / 60, 2)
    natr_bps = r["natr_e8"] / 1e8 * 1e4
    # минутка сигнала: целиком (по минутке, включает часть после сигнала) и на момент сигнала (по сделкам)
    m = int(r["sig_min"])
    o_, h_, l_, c_, qv_ = a[:, m]
    f["sig_min_range_bps_full"] = (h_ - l_) / c_ * 1e4 if c_ == c_ else None
    f["sig_min_body_bps_full"] = s * (c_ - o_) / o_ * 1e4 if o_ == o_ else None
    h1, l1, c1 = a[1, m - 1], a[2, m - 1], a[3, m - 1]
    f["prev_min_range_bps"] = (h1 - l1) / c1 * 1e4 if c1 == c1 else None
    ms0 = T0 + m * MIN
    w = (t >= ms0) & (t <= sm)
    if w.any():
        pp = p[w]
        f["sig_min_range_bps_at_signal"] = (pp.max() - pp.min()) / pp[-1] * 1e4
        f["sig_min_move_bps_at_signal"] = s * (pp[-1] - pp[0]) / pp[0] * 1e4
    else:
        f["sig_min_range_bps_at_signal"] = f["sig_min_move_bps_at_signal"] = None
    f["ret_15m_bps"] = s * (e - a[3, m - 16]) / a[3, m - 16] * 1e4 if a[3, m - 16] == a[3, m - 16] else None
    f["ret_60m_bps"] = s * (e - a[3, m - 61]) / a[3, m - 61] * 1e4 if a[3, m - 61] == a[3, m - 61] else None
    f["turnover_1h_musd"] = float(np.nansum(a[4, m - 60 : m])) / 1e6
    # пересечение уровня
    pre = (t > sm) & (t <= em)
    f["cross_before_entry"] = bool((s * p[pre] >= s * L).any()) if pre.any() else False
    post = (t > em) & (t <= em + 30 * MIN)
    cr = post & (s * p >= s * L)
    f["crossed_30m"] = bool(cr.any())
    f["t_cross_s"] = round((t[cr][0] - em) / 1000, 3) if cr.any() else None
    # MFE / MAE за 30 мин после входа (по сделкам), от цены входа
    if post.any():
        mv = s * (p[post] - e) / e * 1e4
        f["mfe_30m_bps"] = max(0.0, float(mv.max()))
        f["mae_30m_bps"] = max(0.0, float(-mv.min()))
        f["t_mfe_s"] = round((t[post][int(mv.argmax())] - em) / 1000, 1)
        f["mfe_30m_natr"] = f["mfe_30m_bps"] / natr_bps
        f["mae_30m_natr"] = f["mae_30m_bps"] / natr_bps
    else:
        f["mfe_30m_bps"] = f["mae_30m_bps"] = f["t_mfe_s"] = f["mfe_30m_natr"] = f["mae_30m_natr"] = None
    # за всю сделку — по минуткам (входная и выходная минута целиком, приближённо)
    me, mx = int((em - T0) // MIN), int((xm - T0) // MIN)
    hi, lo = np.nanmax(a[1, me : mx + 1]), np.nanmin(a[2, me : mx + 1])
    if s > 0:
        f["mfe_trade_bps_1m"], f["mae_trade_bps_1m"] = (hi - e) / e * 1e4, (e - lo) / e * 1e4
        f["crossed_by_exit_1m"] = bool(hi >= L)
    else:
        f["mfe_trade_bps_1m"], f["mae_trade_bps_1m"] = (e - lo) / e * 1e4, (hi - e) / e * 1e4
        f["crossed_by_exit_1m"] = bool(lo <= L)
    f["mfe_trade_bps_1m"] = max(0.0, f["mfe_trade_bps_1m"])
    f["mae_trade_bps_1m"] = max(0.0, f["mae_trade_bps_1m"])
    # исход
    legs = legs_of(r)
    f["exit_kind"] = r["kind"]
    f["takes"] = int(r["takes"])
    f["hold_s"] = round((xm - em) / 1000, 3)
    f["exit_px"] = legs[-1]["px"]
    f["exit_bps"] = legs[-1]["bps"]
    f["first_take_s"] = round((legs[0]["ms"] - em) / 1000, 3) if r["takes"] > 0 else None
    f["gross_usd"], f["fees_usd"], f["funding_usd"], f["net_usd"] = r["gross"], r["fees"], r["funding"], r["net"]
    f["net_bps"] = r["net"] / NOTIONAL * 1e4
    f["outcome"] = r["kind"] if r["takes"] == 0 or r["kind"] == "forced" else f"{r['kind']}+тейки"
    # проверки целостности: сумма ног = итог движка; вход — реальная сделка сторона-агрессор ± 1 бп
    entry_fee = NOTIONAL * TAKER_BPS / 1e4
    f["chk_fee_diff"] = abs(sum(x["fee"] for x in legs) + entry_fee - r["fees"])
    f["chk_gross_diff"] = abs(sum(x["gross"] for x in legs) - r["gross"])
    at = t == em
    p0 = e / (1 + s * 1e-4)
    f["chk_entry_tick"] = bool(at.any() and (np.abs(p[at] - p0) <= 1.5e-8 + 1e-9 * p0).any())
    return f


def feat_worker(args):
    r, work = args
    tid = r["id"]
    em = r["entry_ms"]
    lo, hi = em - 120_000, em + 30 * MIN
    tp = tick_path(work, tid)
    if os.path.exists(tp):
        tk = load_ticks(work, tid)
    else:
        tk = extract_ticks(r["sym"], lo, hi)
        np.savez_compressed(tp, t=tk[0], p=tk[1], b=tk[2])
    return tid, tk


def cmd_features(a):
    os.makedirs(os.path.join(a.work, "ticks"), exist_ok=True)
    only = set(a.only.split(",")) if a.only else None
    trades = load_trades(a.detail, only)
    print(f"сделок {len(trades)}", flush=True)
    with ThreadPoolExecutor(a.workers) as ex:
        got = {}
        for k, (tid, tk) in enumerate(ex.map(feat_worker, [(r, a.work) for r in trades])):
            got[tid] = tk
            if (k + 1) % 50 == 0:
                print(f"тики {k + 1}/{len(trades)}", flush=True)
    rows = []
    by_sym = defaultdict(list)
    for r in trades:
        by_sym[r["sym"]].append(r)
    for sym, rs in sorted(by_sym.items()):
        kl = klines(sym)
        for r in rs:
            tk = got.get(r["id"]) or load_ticks(a.work, r["id"])
            rows.append(features(r, tk, kl))
    rows.sort(key=lambda x: (x["side"], x["i"]))
    with open(os.path.join(a.work, "features.jsonl"), "w", encoding="utf-8") as fo:
        for f in rows:
            fo.write(json.dumps(f, ensure_ascii=False) + "\n")
    keys = list(rows[0].keys())
    with open(os.path.join(a.work, "features.csv"), "w", newline="", encoding="utf-8") as fo:
        w = csv.DictWriter(fo, fieldnames=keys)
        w.writeheader()
        for f in rows:
            w.writerow({k: ("" if v is None else v) for k, v in f.items()})
    bad = [f["id"] for f in rows if f["chk_fee_diff"] > 1e-6 or f["chk_gross_diff"] > 1e-6]
    noent = [f["id"] for f in rows if not f["chk_entry_tick"]]
    print(f"признаков {len(rows)}; ноги ≠ итог движка: {len(bad)} {bad[:5]}; вход не найден среди сделок: {len(noent)} {noent[:5]}")


# ───────────────────────────── рисование ─────────────────────────────


def setup_mpl():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": "#b9b8b3", "axes.linewidth": 0.8,
        "axes.facecolor": "#fcfcfb", "figure.facecolor": "#fcfcfb", "axes.labelcolor": INK2,
        "xtick.color": INK2, "ytick.color": INK2, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
        "text.parse_math": False,
    })
    return plt


def draw_candles(ax, x, o, h, l, c, px_per_min, lw):
    """Свечи двумя LineCollection (тени и тела): на 10–20 тыс. свечей в разы быстрее ax.bar."""
    from matplotlib.collections import LineCollection

    ok = ~np.isnan(o)
    x, o, h, l, c = x[ok], o[ok], h[ok], l[ok], c[ok]
    col = np.where(c >= o, UP, DOWN)
    span = (h.max() - l.min()) if len(h) else 1.0
    lo = np.minimum(o, c)
    hi = np.maximum(np.maximum(o, c), lo + span * 0.0012)
    wick = np.stack([np.stack([x, l], 1), np.stack([x, h], 1)], 1)
    body = np.stack([np.stack([x, lo], 1), np.stack([x, hi], 1)], 1)
    ax.add_collection(LineCollection(wick, colors=col, linewidths=lw, zorder=3))
    ax.add_collection(LineCollection(body, colors=col, linewidths=0.8 * px_per_min * 72 / 80, capstyle="butt", zorder=3))


def price_axis(ax):
    from matplotlib.ticker import FuncFormatter

    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: fmt_px(v)))


def fmt_hms(ms, ref):
    d = datetime.fromtimestamp((ms + GMT4) / 1000, tz=timezone.utc)
    r = datetime.fromtimestamp((ref + GMT4) / 1000, tz=timezone.utc)
    return d.strftime("%H:%M:%S" if d.date() == r.date() else "%d.%m %H:%M:%S")


def fmt_min(m, secs=False):
    """Минута с 01.09 → «дд.мм ЧЧ:ММ» GMT+4."""
    return datetime.fromtimestamp((T0 + m * MIN + GMT4) / 1000, tz=timezone.utc).strftime("%d.%m %H:%M")


# ───────────────────────────── касания уровня ─────────────────────────────


def find_touches(r, a):
    """Все касания уровня до входа. Касание — минута, у которой high (лонг) / low (шорт) дошёл до нижней (верхней)
    границы полосы допуска ±τ уровня; подряд идущие минуты — одно касание (отметка на самой дальней минуте).
    Окно: от 30 мин до первого пивота движка до минуты входа (не включая её). Пивоты движка — на точной минуте,
    где был экстремум часового бара. → (touches, first_m, entry_m); touches = [{n, m, px, pivot}] по времени."""
    s = 1 if r["side"] == "long" else -1
    L = r["level_px_e8"] / 1e8
    me = int((r["entry_ms"] - T0) // MIN)
    ext = a[1] if s > 0 else a[2]
    pv = []
    for bar, pp in sorted(r["members"]):
        price = pp / 1e8
        seg = np.abs(ext[bar * 60 : bar * 60 + 60] - price)
        seg = np.where(np.isnan(seg), np.inf, seg)
        pv.append((bar * 60 + int(seg.argmin()), price))
    first_m = min(m for m, _ in pv)
    lo = first_m - 30
    band = L * (1 - CFG["tau"]) if s > 0 else L * (1 + CFG["tau"])
    seg = ext[lo:me]
    with np.errstate(invalid="ignore"):
        cond = (seg >= band) if s > 0 else (seg <= band)
    items = [dict(m=m, px=px, pivot=True) for m, px in pv]
    pm = {m for m, _ in pv}
    k = 0
    while k < len(cond):
        if not cond[k]:
            k += 1
            continue
        j = k
        while j < len(cond) and cond[j]:
            j += 1
        grp = seg[k:j]
        if not any((lo + i) in pm for i in range(k, j)):
            i = int(np.nanargmax(grp) if s > 0 else np.nanargmin(grp))
            items.append(dict(m=lo + k + i, px=float(grp[i]), pivot=False))
        k = j
    items.sort(key=lambda d: d["m"])
    for n, d in enumerate(items, 1):
        d["n"] = n
        d["over"] = s * (d["px"] - L) / L  # > 0 — экстремум за уровнем (прокол)
    return items, first_m, me


# ───────────────────────────── рисование ─────────────────────────────


class Placer:
    """Раскладка подписей без наложений в пикселях экрана; подпись соединена тонкой линией с точкой."""

    def __init__(self, ax):
        from matplotlib.text import Text

        self.ax, self.Text = ax, Text
        self.rend = ax.figure.canvas.get_renderer()
        bb = ax.get_window_extent(self.rend)
        self.bounds = (bb.x0 + 3, bb.y0 + 3, bb.x1 - 3, bb.y1 - 3)
        self.rects = []

    def disp(self, x, y):
        return self.ax.transData.transform((x, y))

    def obstacle(self, x, y, half=12):
        px, py = self.disp(x, y)
        self.rects.append((px - half, py - half, px + half, py + half))

    def _free(self, rc):
        b = self.bounds
        if rc[0] < b[0] or rc[2] > b[2] or rc[1] < b[1] or rc[3] > b[3]:
            return False
        return all(rc[2] <= q[0] or rc[0] >= q[2] or rc[3] <= q[1] or rc[1] >= q[3] for q in self.rects)

    def label(self, x, y, text, color, vert=1, side="center", fs=9, weight="bold", gap=22, ec=None, lw=1.2):
        """vert: +1 — искать выше точки, −1 — ниже; side: center — над/под точкой, right — справа от точки."""
        ann = self.ax.annotate(
            text, (x, y), xytext=(0, 0), textcoords="offset pixels", ha="center", va="center", fontsize=fs,
            fontweight=weight, color=color, zorder=30, annotation_clip=False,
            bbox=dict(boxstyle="round,pad=0.28", fc="white", ec=ec or color, lw=lw, alpha=0.96),
            arrowprops=dict(arrowstyle="-", color=color, lw=0.9, alpha=0.85, shrinkA=0, shrinkB=8))
        bb = self.Text.get_window_extent(ann, self.rend)
        w, h = bb.width + 10, bb.height + 8
        ax_, ay_ = self.disp(x, y)
        step = h + 3
        best = None
        for d in (vert, -vert):
            for k in range(0, 60):
                if side == "right":
                    cx = ax_ + gap + w / 2
                    cy = ay_ + d * k * step
                else:
                    cx = ax_
                    cy = ay_ + d * (gap + h / 2 + k * step)
                cx = min(max(cx, self.bounds[0] + w / 2), self.bounds[2] - w / 2)
                rc = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
                if best is None:
                    best = (cx, cy, rc)
                if self._free(rc):
                    best = (cx, cy, rc)
                    break
            else:
                continue
            break
        cx, cy, rc = best
        ann.xyann = (cx - ax_, cy - ay_)
        self.rects.append(rc)
        return ann


EXIT = dict(n_tp=8, t_low=1000, t_high=5000, a=2000, rr=2000, s_pm=5000)  # база b0: NATR в тысячных, стоп в тысячных %


def exit_lines(r, tk, kl, legs):
    """Уровни выхода по правилам движка: жёсткий стоп (−5 %), лестница тейков, стоп в точку входа после 1-го тейка и
    трейл (откат R·NATR от максимума с момента входа; взведён при +A·NATR). Трейл — по минутам (максимум по минуткам,
    во входной минуте — по сделкам после входа): ступенчатая линия, действующая с конца минуты."""
    s = 1 if r["side"] == "long" else -1
    e8, natr = r["entry_px_e8"], r["natr_e8"]

    def dist(xm):
        return e8 * natr * xm // (10**8 * 1000)

    ef = s * e8
    nt = EXIT["n_tp"]
    tps = [(e8 + s * dist(EXIT["t_low"] + ((EXIT["t_high"] - EXIT["t_low"]) * i) // (nt - 1))) / 1e8 for i in range(nt)]
    hard = (e8 - s * (e8 * EXIT["s_pm"] // 100_000)) / 1e8
    a_d, r_d = dist(EXIT["a"]), dist(EXIT["rr"])
    em, xm = r["entry_ms"], r["exit_ms"]
    t, p, bm = tk
    m_e, m_x = int((em - T0) // MIN), int((xm - T0) // MIN)
    p0 = r["entry_px_e8"] / 1e8 / (1 + s * 1e-4)
    best = s * p0
    inm = (t > em) & (t < T0 + (m_e + 1) * MIN)
    if inm.any():
        best = max(best, float((s * p[inm]).max()))
    armed, trail, pts = False, None, []
    for m in range(m_e, m_x + 1):
        if m > m_e:
            ext = kl[1, m] if s > 0 else -kl[2, m]
            if ext == ext:
                best = max(best, float(ext))
        if not armed and (best - ef / 1e8) * 1e8 >= a_d:
            armed = True
        if armed:
            lvl = (best * 1e8 - r_d) / 1e8
            trail = lvl if trail is None else max(trail, lvl)
            if trail * 1e8 > ef:
                pts.append((m + 1.0, s * trail))
    first_take = (legs[0]["ms"] - T0) / MIN if r["takes"] > 0 else None
    return dict(tps=tps, hard=hard, first_take=first_take, trail=pts)


def tick_step(px_per_min):
    for st in (5, 10, 15, 30, 60, 120, 180, 360, 720, 1440, 2880):
        if st * px_per_min >= 150:
            return st
    return 2880


def render(r, kl, tk, out_png, plt):
    from matplotlib.ticker import FixedLocator, FuncFormatter

    s = 1 if r["side"] == "long" else -1
    L = r["level_px_e8"] / 1e8
    e = r["entry_px_e8"] / 1e8
    em, xm = r["entry_ms"], r["exit_ms"]
    legs = legs_of(r)
    touches, first_m, me = find_touches(r, kl)
    hold = (xm - em) / 1000
    x0 = first_m - 30
    x1 = int((xm - T0) // MIN) + 1 + 30
    n = x1 - x0
    px = max(1.2, min(6.0, max(3.0, 1120.0 / n), (24000 - 300) / n))
    PADR = 300
    axw = max(n * px + PADR, 1500)
    LEFT, RIGHT, TOP, BOT = 95, 95, 78, 62
    axh = 700
    W, H = int(axw + LEFT + RIGHT), axh + TOP + BOT
    fig = plt.figure(figsize=(W / 80, H / 80), dpi=80)
    ax = fig.add_axes([LEFT / W, BOT / H, axw / W, axh / H])
    xr = axw / px

    t, o, h_, l_, c = agg_bars(kl, x0, x1, 1)
    xc = (t - T0) / MIN + 0.5
    draw_candles(ax, xc, o, h_, l_, c, px, max(0.5, min(1.0, px * 0.35)))
    ln = exit_lines(r, tk, kl, legs)
    pxs = [g["px"] for g in legs] + [e, L, ln["hard"], max(ln["tps"]), min(ln["tps"])] + [d["px"] for d in touches]
    ymin, ymax = min(np.nanmin(l_), min(pxs)), max(np.nanmax(h_), max(pxs))
    pad = (ymax - ymin) * 0.10
    ax.set_ylim(ymin - pad, ymax + pad)
    ax.set_xlim(x0, x0 + xr)
    ex = (em - T0) / MIN
    xx = (xm - T0) / MIN
    fx = first_m + 0.5

    # полоса допуска (тонкая), уровень: жирная линия от первого касания до входа, дальше тонко
    ax.axhspan(L * (1 - CFG["tau"]), L * (1 + CFG["tau"]), xmin=(fx - x0) / xr, color="#9a998f", alpha=0.20, lw=0, zorder=1)
    ax.hlines(L, fx, ex, color=INK, lw=3.0, zorder=6)
    ax.hlines(L, ex, x0 + xr, color=INK, lw=1.0, ls=(0, (4, 3)), zorder=5)
    # сделка: удержание — заливка, нити от входа к каждому выходу
    ax.axvspan(ex, max(xx, ex + 1.0), color="#6b6a66", alpha=0.13, lw=0, zorder=2)
    for g in legs:
        col = C_TAKE if g["kind"] == "take" else KIND_COLOR[g["kind"]]
        ax.plot([ex, (g["ms"] - T0) / MIN], [e, g["px"]], color=col, lw=0.9, alpha=0.65, zorder=7)

    # уровни выхода: тейки — тонкие зелёные засечки; стоп −5 % → в точку входа после 1-го тейка → трейл
    seg_end = max(xx, ex + 14 / px)
    for tp in ln["tps"]:
        ax.hlines(tp, ex, seg_end, color=C_TAKE, lw=1.0, alpha=0.75, zorder=8)
    ft = ln["first_take"]
    hard_end = max(ft if ft is not None else xx, ex + 10 / px)
    ax.hlines(ln["hard"], ex, hard_end, color=C_STOP, lw=2.0, ls=(0, (5, 3)), zorder=9)
    tr = [pt for pt in ln["trail"] if pt[0] < xx]
    be_end = min(tr[0][0], xx) if tr else xx
    if ft is not None:
        ax.vlines(ft, min(ln["hard"], e), max(ln["hard"], e), color=C_STOP, lw=2.0, ls=(0, (5, 3)), zorder=9)
        ax.hlines(e, ft, max(be_end, ft + 10 / px), color=C_STOP, lw=2.0, ls=(0, (5, 3)), zorder=9)
    if tr:
        xs = [ft if ft is not None else ex] + [q[0] for q in tr] + [xx]
        ys = [e] + [q[1] for q in tr] + [tr[-1][1]]
        ax.step(xs, ys, where="post", color=C_TRAIL, lw=2.2, zorder=9)
    pl = Placer(ax)
    # вход: большая стрелка
    acol = "#0a8f3c" if s > 0 else "#d03b3b"
    ARROW = 78
    ax.annotate("", xy=(ex, e), xytext=(0, -s * ARROW), textcoords="offset pixels", zorder=25, annotation_clip=False,
                xycoords="data", arrowprops=dict(arrowstyle="simple,head_width=1.7,head_length=1.4,tail_width=0.7",
                                                 fc=acol, ec="white", lw=1.3, shrinkA=0, shrinkB=0, mutation_scale=22))
    p_e = pl.disp(ex, e)
    pl.rects.append((p_e[0] - 14, p_e[1] - (ARROW if s > 0 else 0) - 4, p_e[0] + 14, p_e[1] + (0 if s > 0 else ARROW) + 4))
    ent = pl.label(ex, e, f"ВХОД {fmt_px(e)} · {fmt_hms(em, em)}", acol, vert=-s, gap=ARROW + 6, fs=11, ec=acol, lw=2.0)
    ent.arrow_patch.set_visible(False)
    # выходы
    for g in sorted(legs, key=lambda g: g["ms"]):
        take = g["kind"] == "take"
        mk, col = ("o", C_TAKE) if take else (KIND_MARK[g["kind"]], KIND_COLOR[g["kind"]])
        gx = (g["ms"] - T0) / MIN
        ax.plot([gx], [g["px"]], marker=mk, ms=14, mfc=col, mec="white", mew=1.6, zorder=22)
        pl.obstacle(gx, g["px"], 11)
    for g in sorted(legs, key=lambda g: abs(g["px"] - e)):  # от ближних к входу: подписи не пересекают нити
        take = g["kind"] == "take"
        col = C_TAKE if take else KIND_COLOR[g["kind"]]
        gx = (g["ms"] - T0) / MIN
        pl.label(gx, g["px"], f"{g['label']} · {fmt_px(g['px'])} · {g['net']:+.2f} $", col, vert=1 if g["px"] >= e else -1,
                 side="right", fs=9.5, gap=20, ec=col)
    # касания: пивоты движка — залитые круги, остальные — полые; номер внутри
    pl.label(ex, ln["hard"], f"стоп −5 % · {fmt_px(ln['hard'])}", C_STOP, vert=-s, side="center", fs=9.5, gap=16, ec=C_STOP)
    if ft is not None:
        pl.label(ft, e, f"стоп → вход · {fmt_px(e)}", C_STOP, vert=-s, side="center", fs=9, gap=16, ec=C_STOP)
    if tr:
        pl.label(tr[-1][0] if tr[-1][0] < xx else xx, tr[-1][1], f"трейл · {fmt_px(tr[-1][1])}", C_TRAIL, vert=-s, side="center",
                 fs=9, gap=16, ec=C_TRAIL)
    for d in touches:
        tx = d["m"] + 0.5
        ax.plot([tx], [d["px"]], marker="o", ms=19, mfc=INK if d["pivot"] else "white", mec=INK, mew=2.2, zorder=21)
        ax.annotate(str(d["n"]), (tx, d["px"]), ha="center", va="center", fontsize=9 if d["n"] < 10 else 7.5, fontweight="bold",
                    color="white" if d["pivot"] else INK, zorder=23, annotation_clip=False)
        pl.obstacle(tx, d["px"], 12)
    for d in touches:
        tx = d["m"] + 0.5
        kind = "пивот" if d["pivot"] else "касание"
        over = f" · за уровнем +{d['over'] * 100:.1f} %" if d["over"] > CFG["tau"] else ""
        pl.label(tx, d["px"], f"{d['n']}. {kind} · {fmt_min(d['m'])} · {fmt_px(d['px'])}{over}", INK, vert=s,
                 side="center", fs=9 if d["pivot"] else 8.3, weight="bold" if d["pivot"] else "normal", gap=20, ec="#9a998f")
    lab = ax.annotate(f"уровень {fmt_px(L)}", (x0 + xr, L), xytext=(-6, 0), textcoords="offset pixels", ha="right", va="center",
                      fontsize=9.5, fontweight="bold", color=INK, zorder=26,
                      bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=INK, lw=1.0, alpha=0.96))
    # оси
    step = tick_step(px)
    base = (T0 // MIN + 240)  # минут от эпохи до 01.09 00:00 по GMT+4
    first_tick = -(-(x0 + base) // step) * step - base
    ticks = list(range(first_tick, int(x0 + xr), step))
    ax.xaxis.set_major_locator(FixedLocator(ticks))
    fmt = "%d.%m" if step >= 1440 else "%d.%m %H:%M"
    ax.xaxis.set_major_formatter(FuncFormatter(
        lambda v, _: datetime.fromtimestamp((T0 + v * MIN + GMT4) / 1000, tz=timezone.utc).strftime(fmt)))
    price_axis(ax)
    ax.tick_params(labelright=True, right=True, labelsize=9)
    ax.set_xlabel("минутные свечи, время GMT+4", loc="left", fontsize=9)

    net_usd, net_bps = r["net"], r["net"] / NOTIONAL * 1e4
    day = datetime.fromtimestamp((em + GMT4) / 1000, tz=timezone.utc).strftime("%d.%m.%Y")
    title = (f"{r['sym']} · {SIDE_RU[r['side']]} · {day} · итог {net_usd:+.2f} $ ({net_bps:+.1f} бп) · "
             f"{KIND_RU[r['kind']]} · держали {fmt_dur(hold)} · касаний {len(touches)}")
    fig.text(20 / W, 1 - 30 / H, title, fontsize=16, color=INK, fontweight="bold", va="center")
    fig.savefig(out_png, dpi=80)
    plt.close(fig)
    return W, H, int(LEFT + (ex - x0) * px), len(touches)


def cmd_render(a):
    plt = setup_mpl()
    only = set(a.only.split(",")) if a.only else None
    trades = load_trades(a.detail, only)
    os.makedirs(a.out, exist_ok=True)
    by_sym = defaultdict(list)
    for r in trades:
        by_sym[r["sym"]].append(r)
    n, meta = 0, {}
    for sym, rs in sorted(by_sym.items()):
        kl = klines(sym)
        for r in rs:
            W, H, ex, nt = render(r, kl, load_ticks(a.work, r["id"]), os.path.join(a.out, f"{r['id']}.png"), plt)
            meta[r["id"]] = [W, H, ex, nt]
            n += 1
            if n % 25 == 0:
                print(f"PNG {n}/{len(trades)}", flush=True)
    json.dump(meta, open(os.path.join(a.out, f"_meta_{a.tag}.json"), "w"))
    print(f"PNG готово: {n}")


# ───────────────────────────── индекс ─────────────────────────────

INDEX_HTML = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>П-42 — сделки базы b0 (тики 4 мс)</title>
<style>
:root{--bg:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--line:#e4e3df;--acc:#2a78d6;--neg:#c33;--pos:#0a7a0a}
*{box-sizing:border-box}body{margin:0;font:14px/1.35 system-ui,Segoe UI,Arial,sans-serif;background:var(--bg);color:var(--ink)}
header{padding:14px 18px 6px}h1{font-size:18px;margin:0 0 4px}.sub{color:var(--ink2);font-size:13px}
.bar{display:flex;flex-wrap:wrap;gap:10px 14px;align-items:end;padding:8px 18px 12px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:5}
.bar label{display:flex;flex-direction:column;font-size:12px;color:var(--ink2);gap:2px}
.bar input,.bar select{font:inherit;padding:4px 6px;border:1px solid #c9c8c2;border-radius:4px;background:#fff;color:var(--ink)}
.bar input[type=number]{width:84px}.bar input[type=text]{width:120px}
button{font:inherit;padding:5px 10px;border:1px solid #c9c8c2;border-radius:4px;background:#fff;cursor:pointer}
#cnt{margin-left:auto;color:var(--ink2);font-size:13px;white-space:nowrap}
table{border-collapse:collapse;width:100%}th,td{padding:5px 9px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{position:sticky;top:var(--barh,70px);background:#f4f3ef;cursor:pointer;user-select:none;font-weight:600;font-size:12.5px;z-index:4}
th.l,td.l{text-align:left}th .ar{color:var(--acc)}tbody tr{cursor:pointer}tbody tr:hover{background:#eef4fc}
.pos{color:var(--pos)}.neg{color:var(--neg)}
.tag{display:inline-block;padding:0 7px;border-radius:9px;font-size:12px;border:1px solid #c9c8c2}
.t-stop{border-color:#d03b3b;color:#a02222}.t-be{border-color:#6250d6;color:#4a3aa7}.t-trail{border-color:#1baf7a;color:#0b7a55}.t-forced{border-color:#6b6a66;color:#6b6a66}
#modal{display:none;position:fixed;inset:0;background:rgba(15,15,15,.82);z-index:20}#modal.on{display:flex;flex-direction:column;align-items:center}
#modal .top{color:#eee;padding:8px 14px;font-size:13px;display:flex;gap:16px;align-items:center;width:100%}
#modal .top b{font-size:15px}#modal .top .sp{margin-left:auto}
#modal .imgw{flex:1;min-height:0;width:100%;overflow:auto;text-align:center}#modal img{max-width:100%;max-height:100%;background:#fff}
#modal.fit img{max-width:100%;max-height:calc(100vh - 44px)}#modal:not(.fit) img{max-width:none;max-height:none}
</style></head><body>
<header><h1>П-42 · пробой уровня · сделки базы b0 (тики 4 мс, 566 монет, 15.09–13.11)</h1>
<div class="sub">Одна сделка — одна строка. Клик по строке — график (откроется на входе, широкую картинку листать мышью/колесом с Shift); ← → листают отфильтрованный список, Esc — закрыть, Z — вписать в окно / полный размер. Время — GMT+4; позиция $500; комиссии по тарифу владельца.</div></header>
<div class="bar" id="bar">
<label>Монета<input type="text" id="f_sym" placeholder="BTC…"></label>
<label>Сторона<select id="f_side"><option value="">все</option><option value="long">лонг</option><option value="short">шорт</option></select></label>
<label>Исход<select id="f_out"><option value="">все</option></select></label>
<label>Половина<select id="f_half"><option value="">обе</option><option>A</option><option>B</option></select></label>
<label>$ от<input type="number" id="f_min" step="any"></label><label>$ до<input type="number" id="f_max" step="any"></label>
<label>Дата с<input type="date" id="f_d1"></label><label>по<input type="date" id="f_d2"></label>
<label>Уровень<select id="f_cross"><option value="">не важно</option><option value="1">пересёк ≤30 мин</option><option value="0">не пересёк</option></select></label>
<button id="reset">Сброс</button><span id="cnt"></span></div>
<table><thead><tr id="hdr"></tr></thead><tbody id="tb"></tbody></table>
<div id="modal"><div class="top"><b id="m_t"></b><span id="m_s"></span><span class="sp">← → · Esc · Z масштаб · <a id="m_a" style="color:#9cf" target="_blank">открыть файл</a></span></div><div class="imgw"><img id="m_i"></div></div>
<script>
const D=__DATA__;
const COLS=[["#","n",0],["Монета","sym",1],["Сторона","side_ru",1],["Вход (GMT+4)","entry_gmt4",1],["Исход","outcome",1],["Тейков","takes",0],["$ итог","net_usd",0],["бп итог","net_bps",0],
["Держали","hold_s",0],["NATR %","natr_pct",0],["До уровня, бп","dist_entry_bps",0],["Возраст ур., ч","age_h",0],["Пивотов","n_pivots",0],["Касаний","touches",0],["MFE 30м, бп","mfe_30m_bps",0],["MAE 30м, бп","mae_30m_bps",0],["Пересёк","crossed",1]];
const OUTR={stop:"стоп","be":"безубыток",trail:"трейл",forced:"принуд."};
D.forEach((d,i)=>{d.n=i+1;d.side_ru=d.side==="long"?"лонг":"шорт";d.age_h=d.level_age_min/60;d.crossed=d.crossed_30m?"да":"нет";d.date=d.entry_gmt4.slice(0,10)});
const fmtDur=s=>{s=Math.round(s);if(s<60)return s+" с";if(s<3600)return Math.floor(s/60)+" мин "+String(s%60).padStart(2,"0")+" с";const h=Math.floor(s/3600);if(h<48)return h+" ч "+String(Math.floor(s%3600/60)).padStart(2,"0")+" мин";return Math.floor(h/24)+" сут. "+(h%24)+" ч"};
const outs=[...new Set(D.map(d=>d.exit_kind))];const so=document.getElementById("f_out");outs.forEach(o=>{const e=document.createElement("option");e.value=o;e.textContent=OUTR[o]||o;so.appendChild(e)});
let sk="entry_gmt4",sd=1,view=[],cur=-1;
const hdr=document.getElementById("hdr");COLS.forEach(([t,k,l])=>{const th=document.createElement("th");th.className=l?"l":"";th.innerHTML=t+' <span class="ar"></span>';th.onclick=()=>{if(sk===k)sd=-sd;else{sk=k;sd=1}render()};th.dataset.k=k;hdr.appendChild(th)});
const $=id=>document.getElementById(id);
function filt(){const q=$("f_sym").value.trim().toUpperCase(),sd_=$("f_side").value,o=$("f_out").value,h=$("f_half").value,mn=$("f_min").value,mx=$("f_max").value,d1=$("f_d1").value,d2=$("f_d2").value,c=$("f_cross").value;
return D.filter(d=>(!q||d.sym.includes(q))&&(!sd_||d.side===sd_)&&(!o||d.exit_kind===o)&&(!h||d.half===h)&&(mn===""||d.net_usd>=+mn)&&(mx===""||d.net_usd<=+mx)&&(!d1||d.date>=d1)&&(!d2||d.date<=d2)&&(c===""||(c==="1")===d.crossed_30m))}
function cell(d,k){const v=d[k];switch(k){case"net_usd":return `<span class="${v>=0?"pos":"neg"}">${v.toFixed(2)}</span>`;case"net_bps":return `<span class="${v>=0?"pos":"neg"}">${v.toFixed(1)}</span>`;
case"outcome":return `<span class="tag t-${d.exit_kind}">${OUTR[d.exit_kind]||d.exit_kind}${d.takes>0&&d.exit_kind!=="forced"?" + тейки":""}</span>`;case"hold_s":return fmtDur(v);case"entry_gmt4":return v.slice(8,10)+"."+v.slice(5,7)+" "+v.slice(11,19);
case"natr_pct":case"age_h":return v.toFixed(2);case"dist_entry_bps":case"mfe_30m_bps":case"mae_30m_bps":return v==null?"—":v.toFixed(1);default:return v==null?"—":v}}
function render(){view=filt();view.sort((a,b)=>{const x=a[sk],y=b[sk];return (x<y?-1:x>y?1:0)*sd||a.n-b.n});
document.querySelectorAll("th").forEach(th=>th.querySelector(".ar").textContent=th.dataset.k===sk?(sd>0?"▲":"▼"):"");
$("tb").innerHTML=view.map((d,i)=>"<tr data-i='"+i+"'>"+COLS.map(([t,k,l])=>`<td class="${l?"l":""}">${cell(d,k)}</td>`).join("")+"</tr>").join("");
const s=view.reduce((a,d)=>a+d.net_usd,0);$("cnt").textContent=`${view.length} из ${D.length} · сумма ${s.toFixed(2)} $ · средняя ${view.length?(s/view.length).toFixed(2):"—"} $`;}
$("tb").onclick=e=>{const tr=e.target.closest("tr");if(tr)open(+tr.dataset.i)};
function open(i){if(i<0||i>=view.length)return;cur=i;const d=view[i];const im=$("m_i"),iw=document.querySelector(".imgw");im.onload=()=>{iw.scrollLeft=Math.max(0,(d.ex||0)-iw.clientWidth*0.35);iw.scrollTop=0};im.src="charts/"+d.id+".png";$("m_a").href="charts/"+d.id+".png";$("m_t").textContent=`${d.sym} ${d.side_ru} ${d.entry_gmt4.slice(0,19)}`;
$("m_s").textContent=`${d.net_usd.toFixed(2)} $ · ${d.net_bps.toFixed(1)} бп · ${OUTR[d.exit_kind]} · держали ${fmtDur(d.hold_s)} · касаний ${d.touches} · ${i+1}/${view.length}`;$("modal").classList.add("on")}
document.addEventListener("keydown",e=>{if(!$("modal").classList.contains("on"))return;if(e.key==="Escape")$("modal").classList.remove("on");else if(e.key==="ArrowRight")open(cur+1);else if(e.key==="ArrowLeft")open(cur-1);else if(e.key==="z"||e.key==="Z")$("modal").classList.toggle("fit")});
$("modal").onclick=e=>{if(e.target.id==="modal"||e.target.classList.contains("imgw"))$("modal").classList.remove("on")};
document.querySelectorAll(".bar input,.bar select").forEach(el=>el.oninput=render);$("reset").onclick=()=>{document.querySelectorAll(".bar input,.bar select").forEach(el=>el.value="");render()};
document.documentElement.style.setProperty("--barh",document.getElementById("bar").offsetHeight+"px");render();
</script></body></html>
"""


def cmd_index(a):
    rows = [json.loads(x) for x in open(os.path.join(a.work, "features.jsonl"), encoding="utf-8")]
    keep = ["id", "side", "sym", "entry_gmt4", "half", "day", "exit_kind", "takes", "outcome", "net_usd", "net_bps", "hold_s",
            "natr_pct", "dist_entry_bps", "level_age_min", "n_pivots", "touches", "mfe_30m_bps", "mae_30m_bps", "crossed_30m"]
    meta = {}
    cdir = a.charts or os.path.join(a.pub, "charts")
    for fn in os.listdir(cdir) if os.path.isdir(cdir) else []:
        if fn.startswith("_meta_"):
            meta.update(json.load(open(os.path.join(cdir, fn))))
    data = [{k: r[k] for k in keep} for r in rows]
    for d in data:
        m = meta.get(d["id"])
        d["ex"], d["w"] = (m[2], m[0]) if m else (0, 0)
    for d in data:
        for k, v in d.items():
            if isinstance(v, float):
                d[k] = round(v, 4)
    os.makedirs(a.pub, exist_ok=True)
    html = INDEX_HTML.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    open(os.path.join(a.pub, "index.html"), "w", encoding="utf-8").write(html)
    for fn in ("features.csv", "features.jsonl"):
        with open(os.path.join(a.work, fn), "rb") as fi, open(os.path.join(a.pub, fn), "wb") as fo:
            fo.write(fi.read())
    print(f"index.html: {len(data)} сделок")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("features")
    p.add_argument("detail"); p.add_argument("work"); p.add_argument("--only"); p.add_argument("--workers", type=int, default=2)
    p = sub.add_parser("render")
    p.add_argument("detail"); p.add_argument("work"); p.add_argument("--only"); p.add_argument("--out", required=True); p.add_argument("--tag", default="all")
    p = sub.add_parser("index")
    p.add_argument("work"); p.add_argument("pub"); p.add_argument("--charts")
    a = ap.parse_args()
    {"features": cmd_features, "render": cmd_render, "index": cmd_index}[a.cmd](a)


if __name__ == "__main__":
    main()
