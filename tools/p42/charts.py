#!/usr/bin/env python3
"""П-42: график каждой сделки базы b0 (тики 4 мс, §13.4 п. 8) + признаки сделок + HTML-индекс. Запуск на srv.

Вход: `breakout tick` бинаря v12 с `P42_DETAIL=1` → `<detail>/{long,short}-detail.jsonl` (ноги, уровень на сигнале с пивотами,
NATR выхода). Минутки — `/data/klines/binance/<МОНЕТА>/<день>.csv`, сделки — `/data/aggtrades/binance/...zip`.

  python3 charts.py features <detail_dir> <work_dir> [--only id,id] [--workers 2]   # features.jsonl/csv + кэш тиков
  python3 charts.py render   <detail_dir> <work_dir> [--only id,id] [--out DIR]     # PNG на сделку
  python3 charts.py index    <work_dir> <pub_dir>                                   # index.html + features.csv/jsonl + flagstats
  python3 charts.py flagstats <work_dir>                                            # статистика флагов A…G → flagstats.md/json
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
C_TAKE, C_BE, C_TRAIL, C_STOP, C_FORCED = "#008300", "#6250d6", "#0f9fb0", "#d03b3b", "#6b6a66"
KIND_COLOR = {"stop": C_STOP, "be": C_BE, "trail": C_TRAIL, "forced": C_FORCED}


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
    f.update(level_flags(r, a))
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


# ───────────────────────────── флаги аномалий уровня ─────────────────────────────

FLAGS = "ABCDEFG"
TAU = CFG["tau"]
K_PIV = 2  # ширина пивота k (база b0)
FLAG_TXT = {
    "A": "уровень пробит до входа (high/low за уровнем > τ или закрытие минутки за уровнем, от первого пивота до входа)",
    "B": "пивоты ближе 3 ч друг к другу (по минутам экстремумов)",
    "C": "уровень известен < 60 мин до входа (вход − рождение уровня)",
    "D": "диапазон минуты сигнала/входа > 2 NATR",
    "E": "разброс цен пивотов > τ или цена уровня ≠ среднему пивотов",
    "F": "пивот — не экстремум на минутках в окне ±120 мин (проверка движка)",
    "G": "закрытие часового бара после первого пивота и до входа за итоговой ценой уровня (пропуск ретро-проверки при присоединении пивота)",
}
# метрика «силы» флага для отбора примеров: (поле, ...) — больше = сильнее; для B, C меньше = сильнее (знак −1)
FLAG_METRIC = {"A": ("pierce_over_bps", 1), "B": ("min_pivot_gap_min", -1), "C": ("level_known_min", -1),
               "D": ("spike_range_natr", 1), "E": ("pivot_spread_bps", 1), "F": ("pivot_exceed_bps", 1),
               "G": ("late_close_over_bps", 1), "A_post": ("pierce_over_bps_post", 1)}
FLAG_TXT["A_post"] = "из A: прокол/закрытие за уровнем уже после рождения уровня (когда действуют запрет ложного пробоя и проверка закрытий)"


def hour_closes(a, b0, b1):
    """Закрытия часовых баров b0..b1 включительно (последняя непустая минута бара); пустые бары → NaN."""
    if b1 < b0:
        return np.zeros(0)
    seg = a[3, b0 * 60 : (b1 + 1) * 60].reshape(-1, 60)
    out = np.full(len(seg), np.nan)
    for i, row in enumerate(seg):
        v = row[~np.isnan(row)]
        if len(v):
            out[i] = v[-1]
    return out


def ban_sim(r, a, s, L_e8):
    """Проверка движка (tick.rs `candidates`): повторить запрет ложного пробоя на минутках от рождения уровня до минуты сигнала
    (r = 15 мин, f = 0,1 %, цена версии на сигнале). → минута, на которой уровень запрещён, или None."""
    lp = s * L_e8
    hi = np.rint((a[1] if s > 0 else -a[2]) * 1e8)
    cl = np.rint(s * a[3] * 1e8)
    back = lp - (abs(lp) * 100) // 100_000
    pend = None
    for t in range(int(r["born"]), int(r["sig_min"])):
        if not np.isfinite(hi[t]):
            continue
        if pend is not None and t - pend > 15:
            pend = None
        if hi[t] > lp and pend is None:
            pend = t
        if pend is not None and cl[t] < back:
            return t
    return None


def level_flags(r, a):
    """Флаги A…G аномалий уровня + метрики. Цены сравниваются в целых e8, как в движке (часовое закрытие > цены уровня)."""
    s = 1 if r["side"] == "long" else -1
    L_e8 = r["level_px_e8"]
    L = L_e8 / 1e8
    me = int((r["entry_ms"] - T0) // MIN)
    ext = a[1] if s > 0 else a[2]
    pv = pivot_minutes(r, a)
    pm = sorted(m for m, _ in pv)
    first_m = pm[0]
    o = {}
    # A: от минуты первого пивота до минуты входа (не включая её)
    def pierce(m0):
        with np.errstate(invalid="ignore"):
            over = s * (ext[m0:me] - L) / L
            beyond = s * (np.rint(a[3, m0:me] * 1e8) - L_e8) > 0
        return (max(0.0, float(np.nanmax(over))) * 1e4 if np.isfinite(over).any() else 0.0), int(beyond.sum())

    o["pierce_over_bps"], o["pierce_close_n"] = pierce(first_m)
    o["flag_A"] = bool(o["pierce_over_bps"] > TAU * 1e4 or o["pierce_close_n"] > 0)
    # то же, но только после рождения уровня (до него запрет ложного пробоя и проверка закрытий не действуют)
    o["pierce_over_bps_post"], o["pierce_close_n_post"] = pierce(max(first_m, int(r["born"])))
    o["flag_A_post"] = bool(o["pierce_over_bps_post"] > TAU * 1e4 or o["pierce_close_n_post"] > 0)
    # B
    o["min_pivot_gap_min"] = int(min(np.diff(pm))) if len(pm) > 1 else None
    o["flag_B"] = bool(o["min_pivot_gap_min"] is not None and o["min_pivot_gap_min"] < 180)
    # C (рождение — первая версия уровня; цена версии на сигнале могла сложиться позже: верхняя оценка её возраста ниже)
    o["level_known_min"] = int(me - r["born"])
    o["flag_C"] = bool(o["level_known_min"] < 60)
    o["price_age_max_min"] = int(me - (max(b for b, _ in r["members"]) + K_PIV + 1) * 60)
    # D
    nat = r["natr_e8"] / 1e8
    rng = []
    for m in (int(r["sig_min"]), me):
        h_, l_, c_ = a[1, m], a[2, m], a[3, m]
        if c_ == c_:
            rng.append((h_ - l_) / c_)
    o["spike_range_natr"] = max(rng) / nat if rng and nat > 0 else None
    o["flag_D"] = bool(o["spike_range_natr"] is not None and o["spike_range_natr"] > 2)
    # E
    pp_e8 = [x for _, x in r["members"]]
    mean_e8 = sum(pp_e8) // len(pp_e8)
    spread = (max(pp_e8) - min(pp_e8)) / (sum(pp_e8) / len(pp_e8))
    o["pivot_spread_bps"] = spread * 1e4
    o["spread_gt_2tau"] = bool(spread > 2 * TAU * (1 + 1e-6))
    o["level_vs_mean_e8"] = int(L_e8 - mean_e8)
    o["flag_E"] = bool(spread > TAU * (1 + 1e-9) or abs(o["level_vs_mean_e8"]) > 1)
    # F: в окне ±120 мин от минуты пивота нет минуты с более далёким экстремумом
    nbad, exc, nomatch = 0, 0.0, 0
    for m, price in pv:
        w = ext[max(m - 120, 0) : m + 121]
        with np.errstate(invalid="ignore"):
            d = s * (w - price) / price
        if not np.isfinite(ext[m]) or abs(ext[m] - price) > 1e-7 * price:
            nomatch += 1
        if np.isfinite(d).any() and np.nanmax(d) > 1e-9:
            nbad += 1
            exc = max(exc, float(np.nanmax(d)) * 1e4)
    o["pivot_not_extreme_n"], o["pivot_exceed_bps"], o["pivot_px_nomatch_n"] = nbad, exc, nomatch
    o["flag_F"] = bool(nbad > 0 or nomatch > 0)
    # G: закрытия часовых баров fb+1 … последнего, закрытого к минуте входа, относительно итоговой цены уровня
    hc = hour_closes(a, r["first_bar"] + 1, me // 60 - 1)
    with np.errstate(invalid="ignore"):
        ov = s * (np.rint(hc * 1e8) - L_e8)
    o["late_close_beyond_n"] = int((ov > 0).sum())
    o["late_close_over_bps"] = float(np.nanmax(ov)) / L_e8 * 1e4 if (ov > 0).any() else 0.0
    o["flag_G"] = bool(o["late_close_beyond_n"] > 0)
    bt = ban_sim(r, a, s, L_e8)
    o["chk_ban_minute"] = bt
    o["chk_ban_violation"] = bool(bt is not None)
    o["flags"] = "".join(c for c in FLAGS if o["flag_" + c])
    o["n_flags"] = len(o["flags"])
    return o


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


def pivot_minutes(r, a):
    """Пивоты уровня на сигнале → [(минута экстремума на минутках, цена)]: ближайшая к цене пивота минута часового бара."""
    s = 1 if r["side"] == "long" else -1
    ext = a[1] if s > 0 else a[2]
    pv = []
    for bar, pp in sorted(r["members"]):
        price = pp / 1e8
        seg = np.abs(ext[bar * 60 : bar * 60 + 60] - price)
        seg = np.where(np.isnan(seg), np.inf, seg)
        pv.append((bar * 60 + int(seg.argmin()), price))
    return pv


def find_touches(r, a):
    """Все касания уровня до входа. Касание — минута, у которой high (лонг) / low (шорт) дошёл до нижней (верхней)
    границы полосы допуска ±τ уровня; подряд идущие минуты — одно касание (отметка на самой дальней минуте).
    Окно: от 30 мин до первого пивота движка до минуты входа (не включая её). Пивоты движка — на точной минуте,
    где был экстремум часового бара. → (touches, first_m, entry_m); touches = [{n, m, px, pivot}] по времени."""
    s = 1 if r["side"] == "long" else -1
    L = r["level_px_e8"] / 1e8
    me = int((r["entry_ms"] - T0) // MIN)
    ext = a[1] if s > 0 else a[2]
    pv = pivot_minutes(r, a)
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
    return dict(hard=hard, first_take=first_take, trail=pts)


def tick_step(px_per_min):
    for st in (5, 10, 15, 30, 60, 120, 180, 360, 720, 1440, 2880):
        if st * px_per_min >= 150:
            return st
    return 2880


LH = 15  # высота строки подписи в правом поле, px
PXW = 72 / 80  # толщина 1 px в пунктах при dpi 80


def text_w(txt, fs, weight="normal"):
    """Ширина строки в px при dpi 80 (по ней считается ширина правого поля)."""
    from matplotlib.backends.backend_agg import RendererAgg
    from matplotlib.font_manager import FontProperties

    w, _, _ = RendererAgg(10, 10, 80).get_text_width_height_descent(
        txt, FontProperties(family="DejaVu Sans", size=fs, weight=weight), False)
    return w


def usd(v):
    return f"{'+' if v >= 0 else '−'}${abs(v):.2f}"


def dodge(ds, lo, hi, gap=LH):
    """Желаемые y центров подписей ds (px, снизу вверх) → разведённые по вертикали в [lo, hi], порядок сохраняется."""
    order = sorted(range(len(ds)), key=lambda i: -ds[i])  # сверху вниз
    ys, prev = [0.0] * len(ds), None
    for i in order:
        y = min(ds[i], hi - gap / 2) if prev is None else min(ds[i], prev - gap)
        ys[i] = prev = y
    nxt = None
    for i in reversed(order):  # поджать снизу вверх, если ушли ниже поля
        y = max(ys[i], lo + gap / 2 if nxt is None else nxt + gap)
        ys[i] = nxt = y
    return ys


def field_items(r, legs, ln, touches, e, L, ft):
    """Подписи правого поля: items — линии графика [{ys: [цены], text, color, bold}], rows — список касаний."""
    s = 1 if r["side"] == "long" else -1
    acol = "#0a8f3c" if s > 0 else "#d03b3b"
    it = [dict(ys=[L], text=f"уровень {fmt_px(L)}", color=INK, bold=True),
          dict(ys=[e], text=f"вход {fmt_px(e)} · {fmt_hms(r['entry_ms'], r['entry_ms'])}", color=acol, bold=True)]
    last = legs[-1]
    stop_txt = f"стоп −5 % {fmt_px(ln['hard'])}"
    if r["kind"] == "stop":
        stop_txt += f" {usd(last['net'])}"
    elif ft is not None:
        stop_txt += ", после тейка 1 → вход"
    it.append(dict(ys=[ln["hard"]], text=stop_txt, color=C_STOP, bold=False))
    tk = [g for g in legs if g["kind"] == "take"]
    if len(tk) > 3:
        it.append(dict(ys=[g["px"] for g in tk], bold=False, color=C_TAKE,
                       text=f"тейк 1–{len(tk)} {fmt_px(tk[0]['px'])}…{fmt_px(tk[-1]['px'])} {usd(sum(g['net'] for g in tk))}"))
    else:
        it += [dict(ys=[g["px"]], text=f"{g['label']} {fmt_px(g['px'])} {usd(g['net'])}", color=C_TAKE, bold=False) for g in tk]
    if r["kind"] != "stop":
        name = "принуд." if r["kind"] == "forced" else KIND_RU[r["kind"]]
        it.append(dict(ys=[last["px"]], text=f"{name} {fmt_px(last['px'])} {usd(last['net'])}", color=KIND_COLOR[r["kind"]], bold=False))
    rows = []
    for d in touches:
        kind = "пивот" if d["pivot"] else "касание"
        over = f" · за уровнем +{d['over'] * 100:.1f} %" if d["over"] > CFG["tau"] else ""
        rows.append(dict(text=f"{d['n']}. {kind} {fmt_min(d['m'])} {fmt_px(d['px'])}{over}", bold=d["pivot"]))
    return it, rows


def render(r, kl, tk, out_png, plt):
    from matplotlib.lines import Line2D
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
    ln = exit_lines(r, tk, kl, legs)
    ft = ln["first_take"]
    items, rows = field_items(r, legs, ln, touches, e, L, ft)
    # правое поле вне графика: ширина — по самой длинной подписи
    GAP = 34  # от графика до текста: место под выноски
    FIELD = int(GAP + max([text_w(i["text"], 9.5, "bold" if i["bold"] else "normal") for i in items] +
                          [text_w(i["text"], 8.5, "bold" if i["bold"] else "normal") for i in rows] + [150]) + 18)
    PADR = 60
    LEFT, TOP, BOT = 95, 78, 62
    px = max(1.2, min(6.0, max(3.0, 1120.0 / n), (24000 - LEFT - FIELD - PADR) / n))
    axw = max(n * px + PADR, 1500)
    axh = max(700, (len(items) + len(rows) + 3) * LH + 20)
    W, H = int(LEFT + axw + FIELD), axh + TOP + BOT
    fig = plt.figure(figsize=(W / 80, H / 80), dpi=80)
    ax = fig.add_axes([LEFT / W, BOT / H, axw / W, axh / H])
    xr = axw / px

    t, o, h_, l_, c = agg_bars(kl, x0, x1, 1)
    xc = (t - T0) / MIN + 0.5
    draw_candles(ax, xc, o, h_, l_, c, px, max(0.5, min(1.0, px * 0.35)))
    pxs = [g["px"] for g in legs] + [e, L, ln["hard"]] + [d["px"] for d in touches]
    ymin, ymax = min(np.nanmin(l_), min(pxs)), max(np.nanmax(h_), max(pxs))
    pad = (ymax - ymin) * 0.10
    ax.set_ylim(ymin - pad, ymax + pad)
    ax.set_xlim(x0, x0 + xr)
    ex = (em - T0) / MIN
    xx = (xm - T0) / MIN
    fx = first_m + 0.5
    xend = x0 + xr

    # полоса допуска (тонкая), уровень: жирная линия от первого касания до входа, дальше тонко
    ax.axhspan(L * (1 - CFG["tau"]), L * (1 + CFG["tau"]), xmin=(fx - x0) / xr, color="#9a998f", alpha=0.20, lw=0, zorder=1)
    ax.hlines(L, fx, ex, color=INK, lw=3.0, zorder=6)
    ax.hlines(L, ex, xend, color=INK, lw=1.0, ls=(0, (4, 3)), zorder=5)
    ax.axvspan(ex, max(xx, ex + 1.0), color="#6b6a66", alpha=0.13, lw=0, zorder=2)

    def vis(xe):  # отрезок короче 12 px не рисуем: время исполнения — засечкой на конце
        return max(xe, ex + 12 / px)

    def seg(y, xe, col, lw_px, z, ls="-", tick=True):
        ax.hlines(y, ex, xe, color=col, lw=lw_px * PXW, ls=ls, zorder=z)
        if tick:
            ax.plot([xe], [y], marker="|", ms=9, mew=lw_px * PXW, color=col, ls="none", zorder=z + 1)
        ax.hlines(y, xe, xend, color=col, lw=0.7, ls=(0, (1, 3)), alpha=0.55, zorder=z - 1)  # бледное продолжение к выноске

    acol = "#0a8f3c" if s > 0 else "#d03b3b"
    for g in legs:
        if g["kind"] == "take":
            seg(g["px"], vis((g["ms"] - T0) / MIN), C_TAKE, 1.0, 8)
        elif g["kind"] in ("trail", "forced"):
            seg(g["px"], vis(xx), KIND_COLOR[g["kind"]], 1.0, 8)
    # стоп: −5 % до 1-го тейка (или до выхода), дальше ступенька в точку входа
    ds = (0, (5, 3))
    seg(ln["hard"], vis(ft if ft is not None else xx), C_STOP, 1.2, 9, ls=ds, tick=r["kind"] == "stop" and ft is None)
    if ft is not None:
        tr = [pt for pt in ln["trail"] if pt[0] < xx]
        be_end = vis(min(tr[0][0], xx) if tr else xx)
        ax.vlines(ft, min(ln["hard"], e), max(ln["hard"], e), color=C_STOP, lw=1.2 * PXW, ls=ds, zorder=9)
        ax.hlines(e, ft, max(be_end, ft + 12 / px), color=C_STOP, lw=1.2 * PXW, ls=ds, zorder=9)
        if r["kind"] == "be":
            ax.plot([be_end], [legs[-1]["px"]], marker="|", ms=9, mew=1.2 * PXW, color=C_BE, ls="none", zorder=10)
    seg(e, vis(xx), acol, 1.5, 24)  # вход: от времени входа до времени выхода; поверх маркеров касаний

    # касания — маленькие маркеры с номерами (пивоты движка залиты)
    for d in touches:
        tx = d["m"] + 0.5
        ax.plot([tx], [d["px"]], marker="o", ms=13, mfc=INK if d["pivot"] else "white", mec=INK, mew=1.3, ls="none", zorder=21)
        ax.text(tx, d["px"], str(d["n"]), ha="center", va="center", fontsize=6.5 if d["n"] < 10 else 5.5, fontweight="bold",
                color="white" if d["pivot"] else INK, zorder=23)

    # оси
    step = tick_step(px)
    base = (T0 // MIN + 240)  # минут от эпохи до 01.09 00:00 по GMT+4
    first_tick = -(-(x0 + base) // step) * step - base
    ticks = list(range(first_tick, int(xend), step))
    ax.xaxis.set_major_locator(FixedLocator(ticks))
    fmt = "%d.%m" if step >= 1440 else "%d.%m %H:%M"
    ax.xaxis.set_major_formatter(FuncFormatter(
        lambda v, _: datetime.fromtimestamp((T0 + v * MIN + GMT4) / 1000, tz=timezone.utc).strftime(fmt)))
    price_axis(ax)
    ax.tick_params(labelsize=9)
    ax.set_xlabel("минутные свечи, время GMT+4", loc="left", fontsize=9)

    # правое поле: подписи по цене, разведены по вертикали; список касаний — блок сверху или снизу (где меньше сдвигов)
    xr_px = LEFT + axw
    lo, hi = float(BOT), float(BOT + axh)
    yd = [[ax.transData.transform((0, y))[1] for y in it["ys"]] for it in items]
    want = [(min(v) + max(v)) / 2 for v in yd]
    hb = (len(rows) + 1) * LH + 8 if rows else 0
    best = None
    for where in ("top", "bottom"):
        a, b = (lo, hi - hb) if where == "top" else (lo + hb, hi)
        ys = dodge(want, a, b)
        cost = sum(abs(y - w) for y, w in zip(ys, want))
        if best is None or cost < best[0]:
            best = (cost, where, ys)
    _, where, ys = best
    tx_ = xr_px + GAP
    for it, v, y in zip(items, yd, ys):
        col = it["color"]

        def line(xs, yv):
            fig.add_artist(Line2D([q / W for q in xs], [q / H for q in yv], color=col, lw=0.8, alpha=0.6, transform=fig.transFigure))

        for yy in v:
            line([xr_px, xr_px + 10], [yy, yy])
        if len(v) > 1:
            line([xr_px + 10] * 2, [min(v), max(v)])
        line([xr_px + 10, xr_px + GAP - 8, xr_px + GAP - 3], [(min(v) + max(v)) / 2, y, y])
        fig.text(tx_ / W, y / H, it["text"], fontsize=9.5, fontweight="bold" if it["bold"] else "normal", color=col, va="center", ha="left")
    if rows:
        top_y = hi if where == "top" else lo + hb
        fig.text(tx_ / W, (top_y - LH / 2) / H, f"касания ({len(rows)}):", fontsize=8.5, fontweight="bold", color=INK2, va="center", ha="left")
        for k, row in enumerate(rows, 1):
            fig.text(tx_ / W, (top_y - LH / 2 - k * LH) / H, row["text"], fontsize=8.5, fontweight="bold" if row["bold"] else "normal",
                     color=INK, va="center", ha="left")

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
.fl{text-align:center;padding:5px 4px}.fl b{color:#c33}.flh{text-align:center}
#st{padding:6px 18px 10px;border-bottom:1px solid var(--line);font-size:13px}#st summary{cursor:pointer;color:var(--ink2)}
#st table{width:auto;margin-top:6px}#st td,#st th{position:static;cursor:default;padding:3px 9px;font-weight:400;white-space:normal;text-align:right}#st td.l,#st th.l{text-align:left}
#st tr.fr{cursor:pointer}#st tr.fr:hover{background:#eef4fc}#st a{color:var(--acc);cursor:pointer;margin-right:8px}
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
<label>Флаг уровня<select id="f_flag"><option value="">не важно</option><option value="any">любой</option><option value="none">нет флагов</option><option>A</option><option>B</option><option>C</option><option>D</option><option>E</option><option>F</option><option>G</option><option value="A_post">A после рождения</option></select></label>
<button id="reset">Сброс</button><span id="cnt"></span></div>
<details id="st" open><summary>Флаги уровня A–G — статистика (клик по строке — фильтр, клик по id — три примера)</summary><div id="stb"></div></details>
<table><thead><tr id="hdr"></tr></thead><tbody id="tb"></tbody></table>
<div id="modal"><div class="top"><b id="m_t"></b><span id="m_s"></span><span class="sp">← → · Esc · Z масштаб · <a id="m_a" style="color:#9cf" target="_blank">открыть файл</a></span></div><div class="imgw"><img id="m_i"></div></div>
<script>
const D=__DATA__;
const COLS=[["#","n",0],["Монета","sym",1],["Сторона","side_ru",1],["Вход (GMT+4)","entry_gmt4",1],["Исход","outcome",1],["Тейков","takes",0],["$ итог","net_usd",0],["бп итог","net_bps",0],
["Держали","hold_s",0],["NATR %","natr_pct",0],["До уровня, бп","dist_entry_bps",0],["Возраст ур., ч","age_h",0],["Пивотов","n_pivots",0],["Касаний","touches",0],["MFE 30м, бп","mfe_30m_bps",0],["MAE 30м, бп","mae_30m_bps",0],["Пересёк","crossed",1],
["A","flag_A",2],["B","flag_B",2],["C","flag_C",2],["D","flag_D",2],["E","flag_E",2],["F","flag_F",2],["G","flag_G",2]];
const FT=__FLAGTXT__,ST=__FLAGS__;
const OUTR={stop:"стоп","be":"безубыток",trail:"трейл",forced:"принуд."};
D.forEach((d,i)=>{d.n=i+1;d.side_ru=d.side==="long"?"лонг":"шорт";d.age_h=d.level_age_min/60;d.crossed=d.crossed_30m?"да":"нет";d.date=d.entry_gmt4.slice(0,10)});
const fmtDur=s=>{s=Math.round(s);if(s<60)return s+" с";if(s<3600)return Math.floor(s/60)+" мин "+String(s%60).padStart(2,"0")+" с";const h=Math.floor(s/3600);if(h<48)return h+" ч "+String(Math.floor(s%3600/60)).padStart(2,"0")+" мин";return Math.floor(h/24)+" сут. "+(h%24)+" ч"};
const outs=[...new Set(D.map(d=>d.exit_kind))];const so=document.getElementById("f_out");outs.forEach(o=>{const e=document.createElement("option");e.value=o;e.textContent=OUTR[o]||o;so.appendChild(e)});
let sk="entry_gmt4",sd=1,view=[],nav=[],cur=-1;
const hdr=document.getElementById("hdr");COLS.forEach(([t,k,l])=>{const th=document.createElement("th");th.className=l===1?"l":l===2?"flh":"";if(l===2)th.title=FT[k.slice(5)];th.innerHTML=t+' <span class="ar"></span>';th.onclick=()=>{if(sk===k)sd=-sd;else{sk=k;sd=1}render()};th.dataset.k=k;hdr.appendChild(th)});
const $=id=>document.getElementById(id);
function filt(){const q=$("f_sym").value.trim().toUpperCase(),sd_=$("f_side").value,o=$("f_out").value,h=$("f_half").value,mn=$("f_min").value,mx=$("f_max").value,d1=$("f_d1").value,d2=$("f_d2").value,c=$("f_cross").value,fg=$("f_flag").value;
return D.filter(d=>(!fg||(fg==="any"?d.n_flags>0:fg==="none"?d.n_flags===0:d["flag_"+fg]))&&(!q||d.sym.includes(q))&&(!sd_||d.side===sd_)&&(!o||d.exit_kind===o)&&(!h||d.half===h)&&(mn===""||d.net_usd>=+mn)&&(mx===""||d.net_usd<=+mx)&&(!d1||d.date>=d1)&&(!d2||d.date<=d2)&&(c===""||(c==="1")===d.crossed_30m))}
function cell(d,k){const v=d[k];if(k.startsWith("flag_"))return v?"<b>●</b>":"";switch(k){case"net_usd":return `<span class="${v>=0?"pos":"neg"}">${v.toFixed(2)}</span>`;case"net_bps":return `<span class="${v>=0?"pos":"neg"}">${v.toFixed(1)}</span>`;
case"outcome":return `<span class="tag t-${d.exit_kind}">${OUTR[d.exit_kind]||d.exit_kind}${d.takes>0&&d.exit_kind!=="forced"?" + тейки":""}</span>`;case"hold_s":return fmtDur(v);case"entry_gmt4":return v.slice(8,10)+"."+v.slice(5,7)+" "+v.slice(11,19);
case"natr_pct":case"age_h":return v.toFixed(2);case"dist_entry_bps":case"mfe_30m_bps":case"mae_30m_bps":return v==null?"—":v.toFixed(1);default:return v==null?"—":v}}
function render(){view=filt();view.sort((a,b)=>{const x=a[sk],y=b[sk];return (x<y?-1:x>y?1:0)*sd||a.n-b.n});
document.querySelectorAll("#hdr th").forEach(th=>th.querySelector(".ar").textContent=th.dataset.k===sk?(sd>0?"▲":"▼"):"");
$("tb").innerHTML=view.map((d,i)=>"<tr data-i='"+i+"'>"+COLS.map(([t,k,l])=>`<td class="${l===1?"l":l===2?"fl":""}">${cell(d,k)}</td>`).join("")+"</tr>").join("");
const s=view.reduce((a,d)=>a+d.net_usd,0);$("cnt").textContent=`${view.length} из ${D.length} · сумма ${s.toFixed(2)} $ · средняя ${view.length?(s/view.length).toFixed(2):"—"} $`;}
const byId=Object.fromEntries(D.map(d=>[d.id,d]));
(function(){const rows=[...("ABCDEFG".split("")).map(c=>[c,c+" — "+FT[c]]),["A_post","A после рождения уровня (подмножество A)"],["any","любой флаг"],["none","без флагов"],["all","все сделки"]];
let h="<table><thead><tr><th class='l'>Флаг</th><th>сделок</th><th>лонг</th><th>шорт</th><th>сумма $</th><th>средняя $</th><th>прибыльных</th><th class='l'>примеры (id · значение метрики · $)</th></tr></thead><tbody>";
rows.forEach(([k,t])=>{const s=ST[k];h+=`<tr class="fr" data-k="${k}"><td class="l">${t}</td><td>${s.n}</td><td>${s.n_long}</td><td>${s.n_short}</td><td class="${s.sum_usd>=0?"pos":"neg"}">${s.sum_usd.toFixed(2)}</td><td>${s.mean_usd==null?"—":s.mean_usd.toFixed(3)}</td><td>${s.win==null?"—":(s.win*100).toFixed(1)+" %"}</td><td class="l">${(s.examples||[]).map((e,j)=>`<a data-k="${k}" data-j="${j}">${e.id}</a>${e.v} · ${e.net>=0?"+":""}${e.net}`).join(" &nbsp; ")}</td></tr>`});
$("stb").innerHTML=h+"</tbody></table>";
$("stb").onclick=e=>{const a=e.target.closest("a");if(a){e.stopPropagation();nav=ST[a.dataset.k].examples.map(x=>byId[x.id]);open(+a.dataset.j);return}
const tr=e.target.closest("tr.fr");if(tr&&["A","B","C","D","E","F","G","A_post","any","none"].includes(tr.dataset.k)){$("f_flag").value=tr.dataset.k;render()}else if(tr){$("f_flag").value="";render()}}})();
$("tb").onclick=e=>{const tr=e.target.closest("tr");if(tr){nav=view;open(+tr.dataset.i)}};
function flagLine(d){if(!d.n_flags)return " · флагов уровня нет";const t=[];if(d.flag_A)t.push(`A прокол ${d.pierce_over_bps.toFixed(1)} бп, закрытий за уровнем ${d.pierce_close_n}`+(d.flag_A_post?` (после рождения: ${d.pierce_over_bps_post.toFixed(1)} бп, ${d.pierce_close_n_post})`:" (только до рождения уровня)"));if(d.flag_B)t.push(`B пивоты ${d.min_pivot_gap_min} мин друг от друга`);if(d.flag_C)t.push(`C уровень известен ${d.level_known_min} мин`);if(d.flag_D)t.push(`D диапазон ${d.spike_range_natr.toFixed(1)} NATR`);if(d.flag_E)t.push(`E разброс ${d.pivot_spread_bps.toFixed(1)} бп`+(d.level_vs_mean_e8?`, цена ≠ среднему на ${d.level_vs_mean_e8} e8`:""));if(d.flag_F)t.push(`F пивот не экстремум (${d.pivot_not_extreme_n})`);if(d.flag_G)t.push(`G закрытий за уровнем ${d.late_close_beyond_n}, до ${d.late_close_over_bps.toFixed(1)} бп`);return " · флаги: "+t.join("; ")}
function open(i){if(i<0||i>=nav.length)return;cur=i;const d=nav[i];const im=$("m_i"),iw=document.querySelector(".imgw");im.onload=()=>{iw.scrollLeft=Math.max(0,(d.ex||0)-iw.clientWidth*0.35);iw.scrollTop=0};im.src="charts/"+d.id+".png";$("m_a").href="charts/"+d.id+".png";$("m_t").textContent=`${d.sym} ${d.side_ru} ${d.entry_gmt4.slice(0,19)}`;
$("m_s").textContent=`${d.net_usd.toFixed(2)} $ · ${d.net_bps.toFixed(1)} бп · ${OUTR[d.exit_kind]} · держали ${fmtDur(d.hold_s)} · касаний ${d.touches} · ${i+1}/${nav.length}`+flagLine(d);$("modal").classList.add("on")}
document.addEventListener("keydown",e=>{if(!$("modal").classList.contains("on"))return;if(e.key==="Escape")$("modal").classList.remove("on");else if(e.key==="ArrowRight")open(cur+1);else if(e.key==="ArrowLeft")open(cur-1);else if(e.key==="z"||e.key==="Z")$("modal").classList.toggle("fit")});
$("modal").onclick=e=>{if(e.target.id==="modal"||e.target.classList.contains("imgw"))$("modal").classList.remove("on")};
document.querySelectorAll(".bar input,.bar select").forEach(el=>el.oninput=render);$("reset").onclick=()=>{document.querySelectorAll(".bar input,.bar select").forEach(el=>el.value="");render()};
document.documentElement.style.setProperty("--barh",document.getElementById("bar").offsetHeight+"px");render();
</script></body></html>
"""


def _agg(rows):
    n = len(rows)
    tot = sum(x["net_usd"] for x in rows)
    return dict(n=n, n_long=sum(1 for x in rows if x["side"] == "long"), n_short=sum(1 for x in rows if x["side"] == "short"),
                sum_usd=round(tot, 2), mean_usd=round(tot / n, 3) if n else None,
                win=round(sum(1 for x in rows if x["net_usd"] > 0) / n, 4) if n else None)


def flagstats(rows):
    """По каждому флагу: сделок, $, доля прибыльных, 3 самых «сильных» примера; для сравнения — без флагов и все."""
    out = {"all": _agg(rows), "any": _agg([x for x in rows if x["n_flags"] > 0]), "none": _agg([x for x in rows if x["n_flags"] == 0])}
    for c in list(FLAGS) + ["A_post"]:
        sel = [x for x in rows if x["flag_" + c]]
        k, sg = FLAG_METRIC[c]
        top = sorted(sel, key=lambda x: (-sg * (x[k] if x[k] is not None else 0), x["id"]))[:3]
        d = _agg(sel)
        d["metric"] = k
        d["examples"] = [dict(id=x["id"], v=round(x[k], 2) if x[k] is not None else None, net=round(x["net_usd"], 2)) for x in top]
        out[c] = d
    return out


def flagstats_md(st):
    L = ["| флаг | сделок | лонг | шорт | сумма $ | средняя $ | прибыльных | метрика | примеры (id: значение, $) |", "|---|---:|---:|---:|---:|---:|---:|---|---|"]
    for key, name in [(c, f"{c} — {FLAG_TXT[c]}") for c in list(FLAGS) + ["A_post"]] + [("any", "любой флаг"), ("none", "без флагов"), ("all", "все сделки")]:
        d = st[key]
        ex = "; ".join(f"{e['id']}: {e['v']}, {e['net']:+.2f}" for e in d.get("examples", []))
        win = f"{d['win'] * 100:.1f} %" if d["win"] is not None else "—"
        L.append(f"| {name} | {d['n']} | {d['n_long']} | {d['n_short']} | {d['sum_usd']:+.2f} | {d['mean_usd']} | {win} | {d.get('metric', '')} | {ex} |")
    return "\n".join(L) + "\n"


def cmd_flagstats(a):
    rows = [json.loads(x) for x in open(os.path.join(a.work, "features.jsonl"), encoding="utf-8")]
    st = flagstats(rows)
    json.dump(st, open(os.path.join(a.work, "flagstats.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    md = flagstats_md(st)
    open(os.path.join(a.work, "flagstats.md"), "w", encoding="utf-8").write(md)
    print(md)


def cmd_index(a):
    rows = [json.loads(x) for x in open(os.path.join(a.work, "features.jsonl"), encoding="utf-8")]
    keep = ["id", "side", "sym", "entry_gmt4", "half", "day", "exit_kind", "takes", "outcome", "net_usd", "net_bps", "hold_s",
            "natr_pct", "dist_entry_bps", "level_age_min", "n_pivots", "touches", "mfe_30m_bps", "mae_30m_bps", "crossed_30m",
            "flags", "n_flags", "pierce_over_bps", "pierce_close_n", "min_pivot_gap_min", "level_known_min", "spike_range_natr",
            "pierce_over_bps_post", "pierce_close_n_post", "flag_A_post", "pivot_spread_bps", "spread_gt_2tau", "level_vs_mean_e8", "pivot_not_extreme_n", "late_close_beyond_n", "late_close_over_bps"] + ["flag_" + c for c in FLAGS]
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
    st = flagstats(rows)
    json.dump(st, open(os.path.join(a.work, "flagstats.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(os.path.join(a.work, "flagstats.md"), "w", encoding="utf-8").write(flagstats_md(st))
    html = INDEX_HTML.replace("__DATA__", json.dumps(data, ensure_ascii=False)).replace("__FLAGS__", json.dumps(st, ensure_ascii=False)) \
        .replace("__FLAGTXT__", json.dumps(FLAG_TXT, ensure_ascii=False))
    open(os.path.join(a.pub, "index.html"), "w", encoding="utf-8").write(html)
    for fn in ("features.csv", "features.jsonl", "flagstats.md", "flagstats.json"):
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
    p = sub.add_parser("flagstats")
    p.add_argument("work")
    a = ap.parse_args()
    {"features": cmd_features, "render": cmd_render, "index": cmd_index, "flagstats": cmd_flagstats}[a.cmd](a)


if __name__ == "__main__":
    main()
