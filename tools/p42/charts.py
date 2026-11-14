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

UP, DOWN = "#2a78d6", "#e34948"
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


def draw_candles(ax, x, o, h, l, c, width):
    ok = ~np.isnan(o)
    x, o, h, l, c = x[ok], o[ok], h[ok], l[ok], c[ok]
    up = c >= o
    col = np.where(up, UP, DOWN)
    ax.vlines(x, l, h, colors=col, linewidth=0.8, zorder=3)
    span = (h.max() - l.min()) if len(h) else 1.0
    body = np.maximum(np.abs(c - o), span * 0.0015)
    ax.bar(x, body, bottom=np.minimum(o, c), width=width, color=col, linewidth=0, zorder=3)


def time_axis(ax, t0_ms, unit_ms, fmt_days_only=False, n=7):
    """Ось X в единицах unit_ms от t0_ms; подписи GMT+4."""
    from matplotlib.ticker import FuncFormatter, MaxNLocator

    ax.xaxis.set_major_locator(MaxNLocator(n))
    ax.xaxis.set_major_formatter(FuncFormatter(
        lambda v, _: datetime.fromtimestamp((t0_ms + v * unit_ms + GMT4) / 1000, tz=timezone.utc).strftime(
            "%d.%m %H:%M")))


def price_axis(ax):
    from matplotlib.ticker import FuncFormatter

    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: fmt_px(v)))


def fmt_hms(ms, ref):
    d = datetime.fromtimestamp((ms + GMT4) / 1000, tz=timezone.utc)
    r = datetime.fromtimestamp((ref + GMT4) / 1000, tz=timezone.utc)
    return d.strftime("%H:%M:%S" if d.date() == r.date() else "%d.%m %H:%M:%S")


def fan_labels(ax, pts, s, dy0=12, step=13):
    """Подписи ног без наложения: pts — [(x, y, text, color, is_take)], смещения по очереди."""
    for k, (x, y, txt, col, take) in enumerate(pts):
        off = (dy0 + step * (k % 3)) * (s if take else -s) * 1.1
        ax.annotate(txt, (x, y), xytext=(0, off), textcoords="offset points", ha="center",
                    va="bottom" if (off > 0) else "top", fontsize=8.5, color=col, fontweight="bold",
                    arrowprops=dict(arrowstyle="-", color=col, lw=0.5, alpha=0.6) if k % 3 else None)


def level_label(ax, L, s):
    ax.text(0.995, L, f"уровень {fmt_px(L)}", transform=ax.get_yaxis_transform(), va="bottom" if s > 0 else "top",
            ha="right", fontsize=9, color=INK, bbox=dict(fc="#fcfcfb", ec="none", alpha=0.85, pad=1), zorder=10)


def mark_trade(ax, xf, legs, s, e, sp, sig_x, entry_x, entry_txt, kind_off=14):
    """Сигнал, вход и ноги на оси; xf переводит мс → координату X."""
    ax.plot([sig_x], [sp], marker="o", ms=6, mfc="#ffffff", mec=INK, mew=1.2, zorder=7)
    ax.annotate(f"сигнал {fmt_px(sp)}", (sig_x, sp), xytext=(-12, 20 * s), textcoords="offset points", ha="right",
                fontsize=8.5, color=INK, arrowprops=dict(arrowstyle="-", color=INK2, lw=0.6),
                bbox=dict(fc="#fcfcfb", ec="none", alpha=0.8, pad=1))
    ax.plot([entry_x], [e], marker="^" if s > 0 else "v", ms=12, mfc=INK, mec="#fcfcfb", mew=1.0, zorder=8)
    ax.annotate(entry_txt, (entry_x, e), xytext=(-kind_off, -30 * s), textcoords="offset points", ha="right", va="center",
                fontsize=8.5, color=INK, fontweight="bold", arrowprops=dict(arrowstyle="-", color=INK2, lw=0.6),
                bbox=dict(fc="#fcfcfb", ec="none", alpha=0.8, pad=1))
    pts = []
    for g in legs:
        take = g["kind"] == "take"
        mk, col = ("o", C_TAKE) if take else (KIND_MARK[g["kind"]], KIND_COLOR[g["kind"]])
        gx = xf(g["ms"])
        ax.plot([gx], [g["px"]], marker=mk, ms=9, mfc=col, mec="#fcfcfb", mew=1.0, zorder=9)
        pts.append((gx, g["px"], g["label"].replace("тейк ", "т"), col, take))
    fan_labels(ax, pts, s)


def render(r, f, kl, tk, out_png, plt):
    s = 1 if r["side"] == "long" else -1
    L = r["level_px_e8"] / 1e8
    e = r["entry_px_e8"] / 1e8
    em, xm, sm = r["entry_ms"], r["exit_ms"], r["sig_ms"]
    legs = legs_of(r)
    t, p, bm = tk
    hold = (xm - em) / 1000
    zoom = hold <= 20 * 60 and len(t) > 0
    fig = plt.figure(figsize=(15.5, 10.2), dpi=80)
    if zoom:
        ax1 = fig.add_axes([0.055, 0.700, 0.70, 0.165])
        ax2 = fig.add_axes([0.055, 0.395, 0.70, 0.235])
        ax3 = fig.add_axes([0.055, 0.075, 0.70, 0.235])
    else:
        ax1 = fig.add_axes([0.055, 0.640, 0.70, 0.225])
        ax2 = fig.add_axes([0.055, 0.075, 0.70, 0.465])
        ax3 = None
    axt = fig.add_axes([0.775, 0.075, 0.215, 0.79])
    axt.axis("off")

    net_usd, net_bps = r["net"], r["net"] / NOTIONAL * 1e4
    after = "" if r["takes"] == 0 else " после %d тейк." % r["takes"]
    fig.text(0.055, 0.968, f"{r['sym']} · {SIDE_RU[r['side']]} · вход {fmt_ts(em)} GMT+4", fontsize=15, color=INK,
             fontweight="bold", va="center")
    fig.text(0.055, 0.936, f"итог {net_usd:+.2f} $ ({net_bps:+.1f} бп) · {KIND_RU[r['kind']]}{after} · "
             f"держали {fmt_dur(hold)} · NATR {f['natr_pct']:.2f} %", fontsize=12.5, color=INK, va="center")
    fig.text(0.055, 0.909, f"{r['id']} · половина {f['half']} · сутки {f['day']} · позиция $500 · комиссии по тарифу · "
             f"задержка 4 мс", fontsize=9, color=INK2, va="center")

    # ── панель 1: часовые свечи окна уровня
    L_days = CFG["l_days"]
    h0 = int((em - T0 - L_days * DAY) // HOUR)
    h1 = int((min(xm + 2 * HOUR, em + 48 * HOUR) - T0) // HOUR) + 1
    t1, o1, hh, ll, c1 = agg_bars(kl, h0 * 60, h1 * 60, 60)
    x1 = (t1 - T0) / HOUR
    draw_candles(ax1, x1, o1, hh, ll, c1, 0.7)
    xs, xe = x1[0] - 1, x1[-1] + 2
    ax1.axhspan(L * (1 - CFG["tau"]), L * (1 + CFG["tau"]), color="#9a998f", alpha=0.18, lw=0, zorder=1)
    ax1.hlines(L, r["first_bar"], xe, color=INK, lw=1.3, zorder=4)
    level_label(ax1, L, s)
    piv = sorted(r["members"])
    for k, (bar, pp) in enumerate(piv):
        ax1.plot([bar], [pp / 1e8], marker="o", ms=10, mfc="none", mec=INK, mew=1.5, zorder=6)
        ax1.annotate(str(k + 1), (bar, pp / 1e8), xytext=(0, 12 * s), textcoords="offset points", ha="center",
                     va="bottom" if s > 0 else "top", fontsize=9.5, color=INK, fontweight="bold")
    ax1.axvspan((em - T0) / HOUR, max((xm - T0) / HOUR, (em - T0) / HOUR + 0.08), color=INK2, alpha=0.30, lw=0, zorder=2)
    ax1.text((em - T0) / HOUR, 1.0, "вход ", transform=ax1.get_xaxis_transform(), va="top", ha="right", fontsize=8.5, color=INK2)
    ymin = min(np.nanmin(ll), L * (1 - CFG["tau"]))
    ymax = max(np.nanmax(hh), L * (1 + CFG["tau"]))
    pad = (ymax - ymin) * 0.10
    ax1.set_ylim(ymin - pad * (1 if s > 0 else 2.2), ymax + pad * (2.2 if s > 0 else 1))  # место под номера пивотов
    ax1.set_xlim(xs, xe)
    time_axis(ax1, T0, HOUR)
    price_axis(ax1)
    ax1.set_title(f"Часовые свечи за {L_days} сут. до входа (окно уровня): пивотов {len(piv)}, "
                  f"полоса допуска ±{CFG['tau'] * 100:.1f} %; серая полоса — сделка (вход → выход)",
                  loc="left", fontsize=10.5, color=INK, pad=6)

    # ── панель 2: минутки от −60 мин до +30 мин после выхода
    lo_ms, hi_ms = em - 60 * MIN, xm + 30 * MIN
    nmin = (hi_ms - lo_ms) / MIN
    tf = next(v for v in (1, 5, 15, 30, 60, 240) if nmin / v <= 600)
    m0, m1 = int((lo_ms - T0) // MIN), int((hi_ms - T0) // MIN) + 1
    t2, o2, h2, l2, c2 = agg_bars(kl, m0, m1, tf)
    x2 = (t2 - T0) / MIN
    draw_candles(ax2, x2, o2, h2, l2, c2, tf * 0.7)
    xe2, xs2 = x2[-1] + tf * 1.5, x2[0] - tf
    zone_lo, zone_hi = (L * (1 - CFG["e"]), L) if s > 0 else (L, L * (1 + CFG["e"]))
    ax2.axhspan(zone_lo, zone_hi, color="#eda100", alpha=0.20, lw=0, zorder=1)
    ax2.hlines(L, xs2, xe2, color=INK, lw=1.3, zorder=4)
    level_label(ax2, L, s)
    ax2.text(0.005, (zone_lo + zone_hi) / 2, f"зона входа {CFG['e'] * 100:.1f} %", transform=ax2.get_yaxis_transform(),
             va="center", ha="left", fontsize=8.5, color="#8a5d00")
    w = (t >= em - 120_000) & (t <= em + 20_000)
    if w.any():
        ax2.plot((t[w] - T0) / MIN, p[w], color="#222222", lw=0.7, alpha=0.85, zorder=5)
    sp = f["sig_px"]
    mark_trade(ax2, lambda ms: (ms - T0) / MIN, legs, s, e, sp, (sm - T0) / MIN, (em - T0) / MIN,
               f"вход {fmt_px(e)}\n{fmt_ts(em, ms_=True)}")
    ymin = min(np.nanmin(l2), zone_lo, min(g["px"] for g in legs), e)
    ymax = max(np.nanmax(h2), zone_hi, max(g["px"] for g in legs), e)
    pad = (ymax - ymin) * 0.10
    ax2.set_ylim(ymin - pad, ymax + pad)
    ax2.set_xlim(xs2, xe2)
    time_axis(ax2, T0, MIN)
    price_axis(ax2)
    ax2.set_title(f"Свечи {tf} мин (от −60 мин до входа до +30 мин после выхода); тонкая линия — сделки за 2 мин до входа",
                  loc="left", fontsize=10.5, color=INK, pad=6)
    ax2.set_xlabel("" if zoom else "время, GMT+4", fontsize=9)

    # ── панель 3 (только короткие сделки): сделки aggTrades вокруг входа и выхода, секунды от входа
    if zoom:
        padz = max(15.0, hold * 0.6) * 1000
        zlo, zhi = em - padz, xm + padz
        wz = (t >= zlo) & (t <= zhi)
        xz = (t[wz] - em) / 1000
        ax3.plot(xz, p[wz], color="#222222", lw=0.8, drawstyle="steps-post", zorder=5)
        ax3.plot(xz, p[wz], ls="none", marker=".", ms=2.5, color="#222222", zorder=5)
        ax3.axhspan(zone_lo, zone_hi, color="#eda100", alpha=0.20, lw=0, zorder=1)
        ax3.hlines(L, (zlo - em) / 1000, (zhi - em) / 1000, color=INK, lw=1.3, zorder=4)
        level_label(ax3, L, s)
        mark_trade(ax3, lambda ms: (ms - em) / 1000, legs, s, e, sp, (sm - em) / 1000, 0.0, f"вход {fmt_px(e)}")
        vals = list(p[wz]) + [g["px"] for g in legs] + [e, sp, zone_lo, zone_hi]
        ymin, ymax = min(vals), max(vals)
        pad = (ymax - ymin) * 0.12
        ax3.set_ylim(ymin - pad, ymax + pad)
        ax3.set_xlim((zlo - em) / 1000, (zhi - em) / 1000)
        price_axis(ax3)
        ax3.set_title("Сделки aggTrades вокруг входа и выхода (каждая точка — сделка биржи)", loc="left", fontsize=10.5,
                      color=INK, pad=6)
        ax3.set_xlabel("секунды от входа (вход = 0)", fontsize=9)

    # ── правая колонка: выходы и контекст
    y = 0.995

    def line(txt, bold=False, color=INK, dy=0.0235, size=9, mono=False):
        nonlocal y
        axt.text(0.0, y, txt, transform=axt.transAxes, va="top", ha="left", fontsize=size, color=color,
                 fontweight="bold" if bold else "normal", family="DejaVu Sans Mono" if mono else "DejaVu Sans")
        y -= dy

    line("Выходы (позиция $500)", bold=True, size=10.5, dy=0.032)
    line(f"вход {fmt_px(e)}  {fmt_hms(em, em)}.{em % 1000:03d}", size=8.4, dy=0.026, mono=True)
    for g in legs:
        col = C_TAKE if g["kind"] == "take" else KIND_COLOR[g["kind"]]
        nm = g["label"].replace("тейк ", "т")
        line(f"{nm}  {fmt_hms(g['ms'], em)}  {fmt_px(g['px'])}", color=col, bold=True, size=8.4, dy=0.0185, mono=True)
        line(f"   {g['usd'] / NOTIONAL * 100:g} % поз.  {g['net']:+.2f} $  {g['bps']:+.1f} бп", color=INK2, size=8.2,
             dy=0.0245, mono=True)
    line(f"фандинг {r['funding']:+.2f} $, комиссии {r['fees']:.2f} $", color=INK2, size=8.8, dy=0.026)
    line(f"ИТОГО {net_usd:+.2f} $  ({net_bps:+.1f} бп)", bold=True, size=10.5, dy=0.042)
    line("Уровень", bold=True, size=10.5, dy=0.03)
    for k, (bar, pp) in enumerate(piv):
        tb = datetime.fromtimestamp((T0 + bar * HOUR + GMT4) / 1000, tz=timezone.utc).strftime("%d.%m %H:%M")
        line(f"пивот {k + 1}: {tb}  {fmt_px(pp / 1e8)}", size=8.4, dy=0.0195, mono=True)
    y -= 0.012
    line("Контекст входа", bold=True, size=10.5, dy=0.03)
    ctx = [
        ("до уровня при входе", f"{f['dist_entry_bps']:+.1f} бп (на сигнале {f['dist_sig_bps']:.1f})"),
        ("возраст уровня", fmt_dur(f["level_age_min"] * 60)),
        ("пивотов", f"{f['n_pivots']} (разброс {f['pivot_spread_bps']:.0f} бп)"),
        ("NATR 1м/30", f"{f['natr_pct']:.2f} % = {r['natr_e8'] / 1e4:.0f} бп"),
        ("свеча сигнала, размах", f"{f['sig_min_range_bps_full']:.0f} бп" if f["sig_min_range_bps_full"] is not None else "—"),
        ("пересёк уровень ≤30 мин", ("да, через " + fmt_dur(f["t_cross_s"])) if f["crossed_30m"] else "нет"),
        ("MFE / MAE за 30 мин", f"{f['mfe_30m_bps']:+.0f} / −{f['mae_30m_bps']:.0f} бп"),
    ]
    for k, v in ctx:
        line(k, color=INK2, size=8.8, dy=0.0165)
        line("   " + v, color=INK, size=8.8, dy=0.0225)
    fig.savefig(out_png, dpi=80)
    plt.close(fig)


def cmd_render(a):
    plt = setup_mpl()
    only = set(a.only.split(",")) if a.only else None
    trades = load_trades(a.detail, only)
    feats = {}
    for line in open(os.path.join(a.work, "features.jsonl"), encoding="utf-8"):
        d = json.loads(line)
        feats[d["id"]] = d
    os.makedirs(a.out, exist_ok=True)
    by_sym = defaultdict(list)
    for r in trades:
        by_sym[r["sym"]].append(r)
    n = 0
    for sym, rs in sorted(by_sym.items()):
        kl = klines(sym)
        for r in rs:
            tk = load_ticks(a.work, r["id"])
            f = feats[r["id"]]
            png = os.path.join(a.out, f"{r['id']}.png")
            render(r, f, kl, tk, png, plt)
            n += 1
            if n % 25 == 0:
                print(f"PNG {n}/{len(trades)}", flush=True)
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
#modal.fit img{max-height:calc(100vh - 44px)}#modal:not(.fit) img{max-width:none;max-height:none}
</style></head><body>
<header><h1>П-42 · пробой уровня · сделки базы b0 (тики 4 мс, 566 монет, 15.09–13.11)</h1>
<div class="sub">Одна сделка — одна строка. Клик по строке — график; ← → листают отфильтрованный список, Esc — закрыть, Z — масштаб. Время — GMT+4; позиция $500; комиссии по тарифу владельца.</div></header>
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
<div id="modal" class="fit"><div class="top"><b id="m_t"></b><span id="m_s"></span><span class="sp">← → · Esc · Z масштаб · <a id="m_a" style="color:#9cf" target="_blank">открыть файл</a></span></div><div class="imgw"><img id="m_i"></div></div>
<script>
const D=__DATA__;
const COLS=[["#","n",0],["Монета","sym",1],["Сторона","side_ru",1],["Вход (GMT+4)","entry_gmt4",1],["Исход","outcome",1],["Тейков","takes",0],["$ итог","net_usd",0],["бп итог","net_bps",0],
["Держали","hold_s",0],["NATR %","natr_pct",0],["До уровня, бп","dist_entry_bps",0],["Возраст ур., ч","age_h",0],["Пивотов","n_pivots",0],["MFE 30м, бп","mfe_30m_bps",0],["MAE 30м, бп","mae_30m_bps",0],["Пересёк","crossed",1]];
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
function open(i){if(i<0||i>=view.length)return;cur=i;const d=view[i];$("m_i").src="charts/"+d.id+".png";$("m_a").href="charts/"+d.id+".png";$("m_t").textContent=`${d.sym} ${d.side_ru} ${d.entry_gmt4.slice(0,19)}`;
$("m_s").textContent=`${d.net_usd.toFixed(2)} $ · ${d.net_bps.toFixed(1)} бп · ${OUTR[d.exit_kind]} · ${i+1}/${view.length}`;$("modal").classList.add("on")}
document.addEventListener("keydown",e=>{if(!$("modal").classList.contains("on"))return;if(e.key==="Escape")$("modal").classList.remove("on");else if(e.key==="ArrowRight")open(cur+1);else if(e.key==="ArrowLeft")open(cur-1);else if(e.key==="z"||e.key==="Z")$("modal").classList.toggle("fit")});
$("modal").onclick=e=>{if(e.target.id==="modal"||e.target.classList.contains("imgw"))$("modal").classList.remove("on")};
document.querySelectorAll(".bar input,.bar select").forEach(el=>el.oninput=render);$("reset").onclick=()=>{document.querySelectorAll(".bar input,.bar select").forEach(el=>el.value="");render()};
document.documentElement.style.setProperty("--barh",document.getElementById("bar").offsetHeight+"px");render();
</script></body></html>
"""


def cmd_index(a):
    rows = [json.loads(x) for x in open(os.path.join(a.work, "features.jsonl"), encoding="utf-8")]
    keep = ["id", "side", "sym", "entry_gmt4", "half", "day", "exit_kind", "takes", "outcome", "net_usd", "net_bps", "hold_s",
            "natr_pct", "dist_entry_bps", "level_age_min", "n_pivots", "mfe_30m_bps", "mae_30m_bps", "crossed_30m"]
    data = [{k: r[k] for k in keep} for r in rows]
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
    p.add_argument("detail"); p.add_argument("work"); p.add_argument("--only"); p.add_argument("--out", required=True)
    p = sub.add_parser("index")
    p.add_argument("work"); p.add_argument("pub")
    a = ap.parse_args()
    {"features": cmd_features, "render": cmd_render, "index": cmd_index}[a.cmd](a)


if __name__ == "__main__":
    main()
