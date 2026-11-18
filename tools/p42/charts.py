#!/usr/bin/env python3
"""П-42: график каждой сделки базы b0 (тики 4 мс, §13.4 п. 8) + признаки сделок + HTML-индекс. Запуск на srv.

Вход: `breakout tick` бинаря v12 с `P42_DETAIL=1` → `<detail>/{long,short}-detail.jsonl` (ноги, уровень на сигнале с пивотами,
NATR выхода). Минутки — `/data/klines/binance/<МОНЕТА>/<день>.csv`, сделки — `/data/aggtrades/binance/...zip`.

  python3 charts.py features <detail_dir> <work_dir> [--only id,id] [--workers 2] [--old <detail_dir_b0>]   # features.jsonl/csv + кэш тиков
  python3 charts.py render   <detail_dir> <work_dir> [--only id,id] [--out DIR]     # две PNG на сделку: <id>.png (обзор) и <id>_zoom.png (приближение)
  python3 charts.py index    <work_dir> <pub_dir> [--old <detail_dir_b0>]           # index.html + features.csv/jsonl + flagstats
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
OLD_KEYS = None  # (монета, сторона, entry_ms) сделок прежней базы b0 — по ним признак «новая» (--old)

KIND_RU = {"stop": "стоп", "be": "безубыток", "trail": "трейл", "forced": "принудительное закрытие", "tx": "выход по времени", "rt": "выход при возврате под уровень"}
KIND_SHORT = {"stop": "стоп", "be": "безубыток", "trail": "трейл", "forced": "принуд.", "tx": "время", "rt": "возврат"}
SIDE_RU = {"long": "лонг", "short": "шорт"}

UP, DOWN = "#b3c1d3", "#5a626d"
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
C_TAKE, C_BE, C_TRAIL, C_STOP, C_FORCED = "#008300", "#6250d6", "#0f9fb0", "#d03b3b", "#6b6a66"
C_CROSS = "#e07b00"  # переходы цены через уровень (приближение)
CROSS_MAX = 60  # не больше стольких отметок переходов на графике
C_TIME, C_RET = "#7d6608", "#a3478f"  # §13.19: выход по времени, выход при возврате под уровень
KIND_COLOR = {"stop": C_STOP, "be": C_BE, "trail": C_TRAIL, "forced": C_FORCED, "tx": C_TIME, "rt": C_RET}
RULES = {"long": {}, "short": {}}  # §13.19: ключи варианта по сторонам (--rules): tt tb rf rs bm e e2 ls — нужны для линий правил на графике


def rule(r, key, default=0.0):
    return float(RULES.get(r["side"], {}).get(key, default))


def parse_rules(txt):
    """«long:tt=20 tb=0.3;short:bm=1 e2=0.05» → {'long': {...}, 'short': {...}}."""
    out = {"long": {}, "short": {}}
    for part in (txt or "").split(";"):
        if ":" in part:
            side, kv = part.split(":", 1)
            out[side.strip()] = dict(x.split("=", 1) for x in kv.split())
    return out


def rules_text(side):
    return " ".join("%s=%s" % kv for kv in RULES.get(side, {}).items())


def k1(r):
    """Вид выхода лота 1 (при втором лоте `kind` — вид сделки целиком, лот 1 — `kind1`)."""
    return r.get("kind1", r["kind"])


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
            for k in ("entry_ms", "exit_ms", "sig_ms", "old_entry_ms"):  # движок пишет мс от 01.09 00:00 UTC → абсолютные
                if k in r:  # old_entry_* — только у клеток с входом по ленте (§13.16)
                    r[k] += T0
            for g in r["legs"]:
                g[0] += T0
            if r.get("lot2"):  # §13.19 (e2): второй лот — свой вход и выход
                l2 = r["lot2"]
                l2["entry_ms"] += T0
                l2["exit_ms"] += T0
                for g in l2["legs"]:
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


def legs_of(r, lot=1):
    """Ноги с деньгами: [{n, kind, ms, px, u_usd, gross, fee, net, bps}]; entry — отдельно. lot = 2 — второй лот (§13.19, `r["lot2"]`)."""
    s = 1 if r["side"] == "long" else -1
    src = r["lot2"] if lot == 2 else r
    e = src["entry_px_e8"] / 1e8
    out = []
    tk = 0
    for k, (ms, px, u, maker) in enumerate(src["legs"]):
        p = px / 1e8
        usd = u / 1e6
        g = usd * s * (p - e) / e
        fee = usd * (p / e) * (MAKER_BPS if maker else TAKER_BPS) / 1e4
        last = k == len(src["legs"]) - 1
        if last:
            kd = src["kind"] if lot == 2 else k1(r)
            kind, label = kd, KIND_SHORT[kd]
        else:
            tk += 1
            kind, label = "take", f"тейк {tk}"
        out.append(dict(n=k, kind=kind, label=label, ms=ms, px=p, usd=usd, gross=g, fee=fee, net=g - fee,
                        bps=s * (p - e) / e * 1e4, maker=bool(maker)))
    return out


def lot_usd(legs):
    return sum(x["usd"] for x in legs)


def lot_net(legs):
    """Итог лота без фандинга: ноги минус комиссия входа (тейкер) на размер лота."""
    return sum(x["net"] for x in legs) - lot_usd(legs) * TAKER_BPS / 1e4


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
    f["sig_to_fill_s"] = round((em - sm) / 1000, 3)  # от сделки-сигнала до входа (по ленте §13.16 — `lat`; прежняя модель — до сделки, по которой исполнен вход)
    f["is_new"] = None if OLD_KEYS is None else (r["sym"], r["side"], sm) not in OLD_KEYS
    f["entry_model"] = "лента" if "old_entry_ms" in r else "следующая сделка"
    f["sig_ms_abs"] = sm
    if "old_entry_ms" in r:  # §13.16: вход прежней модели на этом же сигнале — время и цена, разность цен (+ = новая хуже)
        oe = r["old_entry_px_e8"] / 1e8
        f["old_entry_gmt4"] = datetime.fromtimestamp((r["old_entry_ms"] + GMT4) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        f["old_entry_px"] = oe
        f["old_sig_to_fill_s"] = round((r["old_entry_ms"] - sm) / 1000, 3)
        f["entry_diff_bp"] = round(s * (e - oe) / oe * 1e4, 3)
        f["old_entry_beyond_level"] = bool(s * oe >= s * L)
    f.update(fresh_admission(r, a))
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
    f["first_take_s"] = round((legs[0]["ms"] - em) / 1000, 3) if len(legs) > 1 else None
    l2 = legs_of(r, 2) if r.get("lot2") else None  # §13.19 (e2)
    f["lot2"] = l2 is not None
    f["exit_kind_ru"] = KIND_RU[r["kind"]]
    f["lot1_kind"] = KIND_SHORT[k1(r)]
    f["lot1_net"] = lot_net(legs)
    f["rules"] = rules_text(r["side"])
    if l2:
        em2 = r["lot2"]["entry_ms"]
        f["lot2_entry_gmt4"] = datetime.fromtimestamp((em2 + GMT4) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        f["lot2_entry_px"] = r["lot2"]["entry_px_e8"] / 1e8
        f["lot2_kind"] = KIND_SHORT[r["lot2"]["kind"]]
        f["lot2_net"] = lot_net(l2)
        f["lot2_exit_px"] = l2[-1]["px"]
    f["gross_usd"], f["fees_usd"], f["funding_usd"], f["net_usd"] = r["gross"], r["fees"], r["funding"], r["net"]
    f["net_bps"] = r["net"] / NOTIONAL * 1e4
    f["outcome"] = r["kind"] if r["takes"] == 0 or r["kind"] == "forced" else f"{r['kind']}+тейки"
    # проверки целостности: сумма ног = итог движка; вход — реальная сделка сторона-агрессор ± 1 бп
    all_legs = legs + (l2 or [])
    entry_fee = (lot_usd(legs) + (lot_usd(l2) if l2 else 0.0)) * TAKER_BPS / 1e4  # лот 1 — $250 даже без лота 2 (§13.19 e2)
    f["chk_fee_diff"] = abs(sum(x["fee"] for x in all_legs) + entry_fee - r["fees"])
    f["chk_gross_diff"] = abs(sum(x["gross"] for x in all_legs) - r["gross"])
    p0 = e / (1 + s * 1e-4)
    if "old_entry_ms" in r:  # §13.16: цена входа = цена последней сделки стороны (агрессор на стороне входа) со временем ≤ времени входа
        side_t = (t <= em) & (bm == (s < 0))
        f["chk_entry_tick"] = bool(side_t.any() and abs(p[side_t][-1] - p0) <= 1.5e-8 + 1e-9 * p0)
    else:
        at = t == em
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
    global OLD_KEYS
    RULES.update(parse_rules(a.rules))
    os.makedirs(os.path.join(a.work, "ticks"), exist_ok=True)
    if a.old:
        OLD_KEYS = {(x["sym"], x["side"], x["sig_ms"]) for x in load_trades(a.old)}
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
    keys = list(dict.fromkeys(k for f in rows for k in f))  # §13.19: поля второго лота есть не у всех строк
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


FR_H_MIN, FR_K, FR_SHARE, FR_B_PM = 240, 120, 50, 500  # §13.14 база: H = 4 ч, K = 120 мин, M/K = 50 %, b = 0,5 % (в тысячных процента)


def fresh_admission(r, a):
    """Какое условие «свежего уровня» (§13.14) допускает сделку: (а) касание уровня (high/low не ниже P − |P|·τ) не старше H в окне от бара
    первого пивота; (б) проторговка — в K минутах до сигнальной минуты не меньше K·M/K закрытий ниже уровня не дальше b % и ни одного на уровне
    или за ним. Цена версии — на сигнале, минута t — сигнальная, закрытые минутки ≤ t−1 (целые e8, как tick.rs `fresh_ok`). Правило — независимая реализация."""
    s = 1 if r["side"] == "long" else -1
    lp = s * r["level_px_e8"]
    t = int(r["sig_min"])
    tau = abs(lp) * 200 // 100_000
    band = lp - tau
    ok = ~np.isnan(a[0])
    hi = np.rint((a[1] if s > 0 else -a[2]) * 1e8)
    cl = np.rint(s * a[3] * 1e8)
    lo_m = max(int(r["first_bar"]) * 60, t - FR_H_MIN)
    touch_a = bool(any(ok[m] and hi[m] >= band for m in range(lo_m, t)))
    lim = abs(lp) * FR_B_PM // 100_000
    need = FR_K * FR_SHARE // 100
    n_in, beyond = 0, False
    for m in range(max(t - FR_K, 0), t):
        if not ok[m]:
            continue
        if cl[m] >= lp:
            beyond = True
            break
        if lp - cl[m] <= lim:
            n_in += 1
    cons_b = (not beyond) and n_in >= need
    return dict(adm_touch=touch_a, adm_cons=cons_b,
                adm="оба" if touch_a and cons_b else "касание" if touch_a else "проторговка" if cons_b else "нет",
                cons_minutes=n_in)


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


def fmt_hms_ms(ms, ref):
    return fmt_hms(ms, ref) + f".{ms % 1000:03d}"


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


def level_crossings(r, tk, kl, x0, x1):
    """Переходы цены через уровень после его рождения в окне минут [x0, x1): [(x в минутах, ms | None, +1 за уровень / −1 обратно)].
    «За уровнем» — лонг: цена ≥ уровня, шорт: цена ≤ уровня. По сделкам — там, где есть кэш тиков ([вход − 120 с, вход + 30 мин]);
    вне его — по минуткам: начало серии минут, где экстремум дошёл до уровня (+1; ms = None, точность до минуты)."""
    s = 1 if r["side"] == "long" else -1
    lp = s * r["level_px_e8"] / 1e8
    born = int(r["born"])
    t, p, _ = tk
    ev, ma, mb, last = [], None, None, False
    if len(t):
        beyond = s * p >= lp
        last = bool(beyond[-1])
        for i in np.nonzero(beyond[1:] != beyond[:-1])[0] + 1:
            if (t[i] - T0) // MIN >= born and x0 <= (t[i] - T0) / MIN < x1:
                ev.append(((t[i] - T0) / MIN, int(t[i]), 1 if beyond[i] else -1))
        ma, mb = int((t[0] - T0) // MIN), int((t[-1] - T0) // MIN)
    ext = kl[1] if s > 0 else -kl[2]
    run = False
    for m in range(max(x0, born), min(x1, ext.shape[0])):
        if ma is not None and ma <= m <= mb:
            run = last  # дальше серия продолжается с состояния на последней сделке кэша
            continue
        v = ext[m]
        if v != v:
            continue
        hit = bool(v >= lp)
        if hit and not run:
            ev.append((m + 0.5, None, 1))
        run = hit
    ev.sort(key=lambda e: e[0])
    return ev


# ───────────────────────────── рисование ─────────────────────────────


EXIT =dict(n_tp=8, t_low=1000, t_high=5000, a=2000, rr=2000, s_pm=5000)  # база b0: NATR в тысячных, стоп в тысячных %


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
    first_take = (legs[0]["ms"] - T0) / MIN if len(legs) > 1 and rule(r, "bm") != 1 else None  # bm=1 — стоп остаётся на −5 %
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


def first_touch_ms(r, tk):
    """Первая сделка после входа с ценой на уровне или за ним (§13.19 «пересекли»), по кэшу тиков; None — нет."""
    s = 1 if r["side"] == "long" else -1
    t, p, _ = tk
    m = (t > r["entry_ms"]) & (s * p >= s * r["level_px_e8"] / 1e8)
    return int(t[m][0]) if m.any() else None


def field_items(r, legs, ln, touches, e, L, ft, zoom=False, cross=None, legs2=None, touch_ms=None):
    """Подписи правого поля: items — линии графика [{ys: [цены], text, color, bold}], rows — список касаний.
    zoom — приближение: каждый тейк и выход отдельной строкой, с временем до мс; cross — переходы уровня (см. level_crossings)."""
    s = 1 if r["side"] == "long" else -1
    acol = "#0a8f3c" if s > 0 else "#d03b3b"
    em = r["entry_ms"]
    tm = (lambda ms: " · " + fmt_hms_ms(ms, em)) if zoom else (lambda ms: "")
    born_txt = f" · возник {fmt_min(r['born'])}" + (" (цена версии на сигнале)" if len(r["members"]) > 2 else "")
    it = [dict(ys=[L], text=f"уровень {fmt_px(L)}{born_txt}", color=INK, bold=True),
          dict(ys=[e], text=f"вход {fmt_px(e)} · {fmt_hms(em, em)}" + (f".{em % 1000:03d}" if "old_entry_ms" in r else ""),
               color=acol, bold=True)]
    if "old_entry_ms" in r:  # §13.16: вход прежней модели («следующая сделка») на том же сигнале — для сравнения
        oe = r["old_entry_px_e8"] / 1e8
        it.append(dict(ys=[oe], text=f"прежний вход {fmt_px(oe)} · {fmt_hms(r['old_entry_ms'], em)} (+{(r['old_entry_ms'] - em) / 1000:.3f} с)",
                       color="#8a6d00", bold=False))
    last = legs[-1]
    if legs2:
        e2 = r["lot2"]["entry_px_e8"] / 1e8
        it.append(dict(ys=[e2], text=f"вход 2 {fmt_px(e2)} · {fmt_hms_ms(r['lot2']['entry_ms'], em)}", color=acol, bold=True))
    stop_txt = f"стоп −5 % {fmt_px(ln['hard'])}"
    if k1(r) == "stop":
        stop_txt += (f" · выход {fmt_px(last['px'])}{tm(last['ms'])}" if zoom else "") + f" {usd(last['net'])}"
    elif ft is not None:
        stop_txt += ", после тейка 1 → вход"
    it.append(dict(ys=[ln["hard"]], text=stop_txt, color=C_STOP, bold=False))
    tk = [g for g in legs if g["kind"] == "take"]
    if len(tk) > 3 and not zoom:
        it.append(dict(ys=[g["px"] for g in tk], bold=False, color=C_TAKE,
                       text=f"тейк 1–{len(tk)} {fmt_px(tk[0]['px'])}…{fmt_px(tk[-1]['px'])} {usd(sum(g['net'] for g in tk))}"))
    else:
        it += [dict(ys=[g["px"]], text=f"{g['label']} {fmt_px(g['px'])}{tm(g['ms'])} {usd(g['net'])}", color=C_TAKE, bold=False) for g in tk]
    if k1(r) != "stop":
        name = "принуд." if k1(r) == "forced" else KIND_RU[k1(r)]
        it.append(dict(ys=[last["px"]], text=f"{name} {fmt_px(last['px'])}{tm(last['ms'])} {usd(last['net'])}", color=KIND_COLOR[k1(r)], bold=False))
    if legs2:  # лот 2: свои тейки и выход
        for g in legs2:
            if g["kind"] == "take":
                it.append(dict(ys=[g["px"]], text=f"лот 2 {g['label']} {fmt_px(g['px'])}{tm(g['ms'])} {usd(g['net'])}", color=C_TAKE, bold=False))
            else:
                nm = "принуд." if g["kind"] == "forced" else KIND_RU[g["kind"]]
                it.append(dict(ys=[g["px"]], text=f"лот 2: {nm} {fmt_px(g['px'])}{tm(g['ms'])} {usd(g['net'])}", color=KIND_COLOR[g["kind"]], bold=False))
    if rule(r, "rf") > 0 and touch_ms is not None:
        thr = L * (1 - s * rule(r, "rf") / 100)
        it.append(dict(ys=[thr], text=f"порог возврата −{rule(r, 'rf'):g} % от уровня {fmt_px(thr)} · ≥ {rule(r, 'rs'):g} с", color=C_RET, bold=False))
    if cross is not None:
        after = [c for c in cross if c[0] >= (em - T0) / MIN - 1e-9 and c[2] > 0]
        if after:
            c = after[0]
            when = fmt_hms_ms(c[1], em) if c[1] is not None else f"{fmt_min(int(c[0]))} (минутка)"
            txt = f"вышла за уровень {when}"
        else:
            txt = "после входа за уровень не выходила"
        txt += f" · переходов в окне {len(cross)}" + (f" (показаны {CROSS_MAX})" if len(cross) > CROSS_MAX else "")
        it.append(dict(ys=[L], text=txt, color=C_CROSS, bold=False))
    rows = []
    for d in touches:
        kind = "пивот" if d["pivot"] else "касание"
        over = f" · за уровнем +{d['over'] * 100:.1f} %" if d["over"] > CFG["tau"] else ""
        rows.append(dict(text=f"{d['n']}. {kind} {fmt_min(d['m'])} {fmt_px(d['px'])}{over}", bold=d["pivot"]))
    return it, rows


def render(r, kl, tk, out_png, plt, zoom=False):
    """График сделки. Обзор — от 1-го пивота до выхода + 30 мин; zoom — приближение: от 30 мин до сигнала до 30 мин после выхода
    (всегда ≥ 1 ч), минутные свечи, каждый тейк и выход маркером с ценой и временем, переходы цены через уровень.
    Линия уровня и полоса ±τ — от рождения уровня (`born` движка), не от первого пивота; пивоты — маркеры."""
    from matplotlib.lines import Line2D
    from matplotlib.ticker import FixedLocator, FuncFormatter

    s = 1 if r["side"] == "long" else -1
    L = r["level_px_e8"] / 1e8
    e = r["entry_px_e8"] / 1e8
    em, xm, sm = r["entry_ms"], r["exit_ms"], r["sig_ms"]
    born = int(r["born"])
    legs = legs_of(r)
    touches, first_m, me = find_touches(r, kl)
    hold = (xm - em) / 1000
    x1 = int((xm - T0) // MIN) + 1 + 30
    x0 = max(int((sm - T0) // MIN) - 30, 0) if zoom else first_m - 30
    x1 = min(max(x1, x0 + 60), kl.shape[1])
    n = x1 - x0
    if zoom:
        touches = [d for d in touches if x0 <= d["m"] < x1]
        cross = level_crossings(r, tk, kl, x0, x1)
    else:
        cross = None
    ln = exit_lines(r, tk, kl, legs)
    ft = ln["first_take"]
    legs2 = legs_of(r, 2) if r.get("lot2") else None
    touch_ms = first_touch_ms(r, tk) if (rule(r, "rf") > 0 or rule(r, "tt") > 0) else None
    items, rows = field_items(r, legs, ln, touches, e, L, ft, zoom, cross, legs2, touch_ms)
    ex = (em - T0) / MIN
    xx = (xm - T0) / MIN
    xs = (sm - T0) / MIN
    pxs = [g["px"] for g in legs] + [e, L] + [d["px"] for d in touches] + ([g["px"] for g in legs2] + [r["lot2"]["entry_px_e8"] / 1e8] if legs2 else [])
    if "old_entry_ms" in r:
        pxs.append(r["old_entry_px_e8"] / 1e8)
    t, o, h_, l_, c = agg_bars(kl, x0, x1, 1)
    ymin, ymax = min(np.nanmin(l_), min(pxs)), max(np.nanmax(h_), max(pxs))
    show_hard = True
    if zoom and k1(r) != "stop" and not (ymin <= ln["hard"] <= ymax):  # приближение: стоп −5 % далеко — осью не растягиваем
        show_hard = False
        items = [i for i in items if not (i["color"] == C_STOP and i["ys"] == [ln["hard"]])]
    else:
        ymin, ymax = min(ymin, ln["hard"]), max(ymax, ln["hard"])
    # правое поле вне графика: ширина — по самой длинной подписи
    GAP = 34  # от графика до текста: место под выноски
    FIELD = int(GAP + max([text_w(i["text"], 9.5, "bold" if i["bold"] else "normal") for i in items] +
                          [text_w(i["text"], 8.5, "bold" if i["bold"] else "normal") for i in rows] + [150]) + 18)
    PADR = 60
    LEFT, TOP, BOT = 95, 78, 62
    if zoom:
        px = max(1.0, min(14.0, 1400.0 / n, (24000 - LEFT - FIELD - PADR) / n))
        axw = max(n * px + PADR, 1100)
    else:
        px = max(1.2, min(6.0, max(3.0, 1120.0 / n), (24000 - LEFT - FIELD - PADR) / n))
        axw = max(n * px + PADR, 1500)
    axh = max(700, (len(items) + len(rows) + 3) * LH + 20)
    W, H = int(LEFT + axw + FIELD), axh + TOP + BOT
    fig = plt.figure(figsize=(W / 80, H / 80), dpi=80)
    ax = fig.add_axes([LEFT / W, BOT / H, axw / W, axh / H])
    xr = axw / px

    xc = (t - T0) / MIN + 0.5
    draw_candles(ax, xc, o, h_, l_, c, px, max(0.5, min(1.0, px * 0.35)))
    pad = (ymax - ymin) * 0.10
    ax.set_ylim(ymin - pad, ymax + pad)
    ax.set_xlim(x0, x0 + xr)
    xend = x0 + xr
    lx = max(born, x0)  # линия и полоса — с рождения уровня

    # полоса допуска (тонкая), уровень: жирная линия от рождения до входа, дальше тонко
    ax.axhspan(L * (1 - CFG["tau"]), L * (1 + CFG["tau"]), xmin=(lx - x0) / xr, color="#9a998f", alpha=0.20, lw=0, zorder=1)
    ax.hlines(L, lx, ex, color=INK, lw=3.0, zorder=6)
    ax.hlines(L, ex, xend, color=INK, lw=1.0, ls=(0, (4, 3)), zorder=5)
    ax.axvspan(ex, max(xx, ex + 1.0), color="#6b6a66", alpha=0.13, lw=0, zorder=2)
    # вертикальные метки: рождение уровня (если в окне), сигнал (приближение)
    lab = dict(fontsize=8.5, color=INK2, zorder=30, transform=ax.get_xaxis_transform(), bbox=dict(fc="#fcfcfb", ec="none", alpha=0.85, pad=1.5))
    if x0 <= born < xend:
        ax.axvline(born, color=INK2, lw=1.2, ls=(0, (1, 2)), zorder=4)
        right = (born - x0) / xr > 0.8
        ax.text(born, 0.985, fmt_min(born) + " уровень возник" if right else "уровень возник " + fmt_min(born),
                ha="right" if right else "left", va="top", **lab)
    if zoom:
        ax.axvline(xs, color=INK2, lw=1.0, ls=(0, (3, 3)), zorder=4)
        ax.text(xs, 0.945, "сигнал " + fmt_hms_ms(sm, em), ha="left", va="top", **lab)

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
    if show_hard:
        seg(ln["hard"], vis(ft if ft is not None else xx), C_STOP, 1.2, 9, ls=ds, tick=k1(r) == "stop" and ft is None)
    if ft is not None:
        tr = [pt for pt in ln["trail"] if pt[0] < xx]
        be_end = vis(min(tr[0][0], xx) if tr else xx)
        if show_hard:
            ax.vlines(ft, min(ln["hard"], e), max(ln["hard"], e), color=C_STOP, lw=1.2 * PXW, ls=ds, zorder=9)
        ax.hlines(e, ft, max(be_end, ft + 12 / px), color=C_STOP, lw=1.2 * PXW, ls=ds, zorder=9)
        if k1(r) == "be":
            ax.plot([be_end], [legs[-1]["px"]], marker="|", ms=9, mew=1.2 * PXW, color=C_BE, ls="none", zorder=10)
    x1_lot1 = (legs[-1]["ms"] - T0) / MIN if legs2 else xx
    seg(e, vis(x1_lot1), acol, 1.5, 24)  # вход: от времени входа до времени выхода лота 1; поверх маркеров касаний
    if legs2:  # §13.19 (e2): вход 2 — белый треугольник с рамкой цвета стороны; линия до выхода лота 2
        e2, ex2 = r["lot2"]["entry_px_e8"] / 1e8, (r["lot2"]["entry_ms"] - T0) / MIN
        xx2 = (r["lot2"]["exit_ms"] - T0) / MIN
        ax.hlines(e2, ex2, max(xx2, ex2 + 12 / px), color=acol, lw=1.5 * PXW, ls=(0, (6, 2)), zorder=24)
        ax.plot([ex2], [e2], marker="^" if s > 0 else "v", ms=11, mfc="white", mec=acol, mew=1.8, ls="none", zorder=28)
        ax.text(ex2, e2, "2", ha="center", va="center", fontsize=6, fontweight="bold", color=acol, zorder=29)
        for g in legs2:
            if g["kind"] == "take":
                seg(g["px"], vis((g["ms"] - T0) / MIN), C_TAKE, 1.0, 8, ls=(0, (6, 2)))
    if rule(r, "rf") > 0 and touch_ms is not None:  # порог возврата — от первого касания до выхода
        thr = L * (1 - s * rule(r, "rf") / 100)
        ax.hlines(thr, (touch_ms - T0) / MIN, max(xx, (touch_ms - T0) / MIN + 12 / px), color=C_RET, lw=1.0, ls=(0, (1, 2)), zorder=7)
    if rule(r, "tt") > 0 and touch_ms is not None:  # срок выхода по времени — T мин от первого касания
        xd = (touch_ms - T0) / MIN + rule(r, "tt")
        if x0 < xd < xend:
            ax.axvline(xd, color=C_TIME, lw=1.0, ls=(0, (1, 2)), zorder=4)
            ax.text(xd, 0.905, f"срок {rule(r, 'tt'):g} мин от касания (пробой {rule(r, 'tb'):g} %)", ha="left", va="top", **lab)
    if "old_entry_ms" in r:  # §13.16: ромб — где вошла бы прежняя модель («следующая сделка стороны»)
        ax.plot([(r["old_entry_ms"] - T0) / MIN], [r["old_entry_px_e8"] / 1e8], marker="D", ms=7, mfc="white", mec="#8a6d00", mew=1.6, ls="none", zorder=26)

    # касания — маленькие маркеры с номерами (пивоты движка залиты)
    for d in touches:
        tx = d["m"] + 0.5
        ax.plot([tx], [d["px"]], marker="o", ms=13, mfc=INK if d["pivot"] else "white", mec=INK, mew=1.3, ls="none", zorder=21)
        ax.text(tx, d["px"], str(d["n"]), ha="center", va="center", fontsize=6.5 if d["n"] < 10 else 5.5, fontweight="bold",
                color="white" if d["pivot"] else INK, zorder=23)

    if zoom:  # переходы цены через уровень; вход, тейки, выход — маркеры по точному времени и цене
        for cx, _, _ in cross[:CROSS_MAX]:
            ax.plot([cx], [L], marker="X", ms=9, mfc=C_CROSS, mec="white", mew=0.9, ls="none", zorder=27)
        ax.plot([ex], [e], marker="^" if s > 0 else "v", ms=11, mfc=acol, mec="white", mew=1.0, ls="none", zorder=28)
        for g in legs:
            gx = (g["ms"] - T0) / MIN
            if g["kind"] == "take":
                ax.plot([gx], [g["px"]], marker="o", ms=13, mfc=C_TAKE, mec="white", mew=1.0, ls="none", zorder=28)
                ax.text(gx, g["px"], str(g["n"] + 1), ha="center", va="center", fontsize=6.5, fontweight="bold", color="white", zorder=29)
            else:
                if g["kind"] in ("stop", "be"):
                    ax.hlines(g["px"], gx, xend, color=KIND_COLOR[g["kind"]], lw=0.7, ls=(0, (1, 3)), alpha=0.55, zorder=7)

    for lg in [legs] + ([legs2] if legs2 else []):  # явный маркер каждого выхода — квадрат цвета вида и подпись вида (§13.19)
        g = lg[-1]
        gx = (g["ms"] - T0) / MIN
        ax.plot([gx], [g["px"]], marker="s", ms=11, mfc=KIND_COLOR[g["kind"]], mec="white", mew=1.0, ls="none", zorder=28)
        ax.text(gx, g["px"], "  " + KIND_SHORT[g["kind"]] + (" (лот 2)" if lg is not legs else ""), ha="left", va="bottom", fontsize=8.5, fontweight="bold",
                color=KIND_COLOR[g["kind"]], zorder=30, clip_on=True)
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

        def line(xs_, yv):
            fig.add_artist(Line2D([q / W for q in xs_], [q / H for q in yv], color=col, lw=0.8, alpha=0.6, transform=fig.transFigure))

        for yy in v:
            line([xr_px, xr_px + 10], [yy, yy])
        if len(v) > 1:
            line([xr_px + 10] * 2, [min(v), max(v)])
        line([xr_px + 10, xr_px + GAP - 8, xr_px + GAP - 3], [(min(v) + max(v)) / 2, y, y])
        fig.text(tx_ / W, y / H, it["text"], fontsize=9.5, fontweight="bold" if it["bold"] else "normal", color=col, va="center", ha="left")
    if rows:
        top_y = hi if where == "top" else lo + hb
        fig.text(tx_ / W, (top_y - LH / 2) / H, f"касания в окне ({len(rows)}):" if zoom else f"касания ({len(rows)}):",
                 fontsize=8.5, fontweight="bold", color=INK2, va="center", ha="left")
        for k, row in enumerate(rows, 1):
            fig.text(tx_ / W, (top_y - LH / 2 - k * LH) / H, row["text"], fontsize=8.5, fontweight="bold" if row["bold"] else "normal",
                     color=INK, va="center", ha="left")

    net_usd, net_bps = r["net"], r["net"] / NOTIONAL * 1e4
    day = datetime.fromtimestamp((em + GMT4) / 1000, tz=timezone.utc).strftime("%d.%m.%Y")
    title = (f"{r['sym']} · {SIDE_RU[r['side']]} · {day} · итог {net_usd:+.2f} $ ({net_bps:+.1f} бп) · "
             f"{KIND_RU[r['kind']]}" + (" · 2 лота" if legs2 else "") + f" · держали {fmt_dur(hold)} · касаний {len(touches)}" + (" · приближение" if zoom else ""))
    fig.text(20 / W, 1 - 30 / H, title, fontsize=16, color=INK, fontweight="bold", va="center")
    fig.savefig(out_png, dpi=80)
    plt.close(fig)
    return W, H, int(LEFT + (ex - x0) * px), len(touches)


def render_secs(r, tk, out_png, plt):
    """§13.19: сделка короче 2 мин — график в секундном масштабе: сделки ленты точками (светлая — покупатель-тейкер, тёмная — продавец-тейкер) и ступенью цены,
    уровень, вход (и вход 2), тейки, выходы квадратами цвета вида с подписью (время до мс, цена, $), порог возврата, стоп."""
    from matplotlib.lines import Line2D
    from matplotlib.ticker import FuncFormatter

    s = 1 if r["side"] == "long" else -1
    L = r["level_px_e8"] / 1e8
    e = r["entry_px_e8"] / 1e8
    em, sm = r["entry_ms"], r["sig_ms"]
    legs = legs_of(r)
    legs2 = legs_of(r, 2) if r.get("lot2") else None
    xm = r["exit_ms"]
    t, p, bm = tk
    hold_ms = xm - em
    lo, hi = min(sm, em) - max(3000, int(0.15 * hold_ms)), xm + max(3000, int(0.3 * hold_ms))  # окно растёт с удержанием, но остаётся секундным
    w = (t >= lo) & (t <= hi)
    hard = (r["entry_px_e8"] - s * (r["entry_px_e8"] * EXIT["s_pm"] // 100_000)) / 1e8
    touch_ms = first_touch_ms(r, tk)
    evp = [e, L] + [g["px"] for g in legs] + ([r["lot2"]["entry_px_e8"] / 1e8] + [g["px"] for g in legs2] if legs2 else [])
    ymin, ymax = min(list(p[w]) + evp), max(list(p[w]) + evp)
    items = [dict(ys=[L], text=f"уровень {fmt_px(L)}", color=INK, bold=True),
             dict(ys=[e], text=f"вход {fmt_px(e)} · {fmt_hms_ms(em, em)}", color="#0a8f3c" if s > 0 else "#d03b3b", bold=True)]
    if legs2:
        items.append(dict(ys=[r["lot2"]["entry_px_e8"] / 1e8], text=f"вход 2 {fmt_px(r['lot2']['entry_px_e8'] / 1e8)} · {fmt_hms_ms(r['lot2']['entry_ms'], em)}",
                          color="#0a8f3c" if s > 0 else "#d03b3b", bold=True))
    for tag, lg in (("", legs), ("лот 2 ", legs2 or [])):
        for g in lg:
            nm = g["label"] if g["kind"] == "take" else ("принуд." if g["kind"] == "forced" else KIND_RU[g["kind"]])
            items.append(dict(ys=[g["px"]], text=f"{tag}{nm} {fmt_px(g['px'])} · {fmt_hms_ms(g['ms'], em)} {usd(g['net'])}",
                              color=C_TAKE if g["kind"] == "take" else KIND_COLOR[g["kind"]], bold=False))
    hard_in = ymin <= hard <= ymax  # стоп −5 % — осью не растягиваем: далеко от цены
    items.append(dict(ys=[min(max(hard, ymin), ymax)], text=f"стоп −5 % {fmt_px(hard)}" + ("" if hard_in else " (вне графика)"), color=C_STOP, bold=False))
    thr = None
    if rule(r, "rf") > 0 and touch_ms is not None:
        thr = L * (1 - s * rule(r, "rf") / 100)
        ymin, ymax = min(ymin, thr), max(ymax, thr)
        items.append(dict(ys=[thr], text=f"порог возврата −{rule(r, 'rf'):g} % от уровня {fmt_px(thr)} · ≥ {rule(r, 'rs'):g} с", color=C_RET, bold=False))
    GAP, LEFT, TOP, BOT, PADR = 34, 95, 78, 62, 40
    FIELD = int(GAP + max([text_w(i["text"], 9.5, "bold" if i["bold"] else "normal") for i in items] + [150]) + 18)
    axw = 1400
    axh = max(620, (len(items) + 3) * LH + 20)
    W, H = int(LEFT + axw + FIELD), axh + TOP + BOT
    fig = plt.figure(figsize=(W / 80, H / 80), dpi=80)
    ax = fig.add_axes([LEFT / W, BOT / H, axw / W, axh / H])
    pad = (ymax - ymin) * 0.12 or ymax * 1e-4
    ax.set_ylim(ymin - pad, ymax + pad)
    x0, x1 = (lo - em) / 1000.0, (hi - em) / 1000.0
    ax.set_xlim(x0, x1)
    tt, pp, bb = (t[w] - em) / 1000.0, p[w], bm[w]
    ax.step(tt, pp, where="post", color="#9aa3ae", lw=0.8, zorder=3)
    ax.scatter(tt[~bb], pp[~bb], s=9, color=UP, edgecolors="#6f7d90", linewidths=0.4, zorder=4)  # покупатель-тейкер
    ax.scatter(tt[bb], pp[bb], s=9, color=DOWN, zorder=4)  # продавец-тейкер
    ax.hlines(L, x0, x1, color=INK, lw=2.0, zorder=5)
    if hard_in:
        ax.hlines(hard, 0, (legs[-1]["ms"] - em) / 1000.0, color=C_STOP, lw=1.2, ls=(0, (5, 3)), zorder=6)
    if thr is not None:
        ax.hlines(thr, (touch_ms - em) / 1000.0, x1, color=C_RET, lw=1.0, ls=(0, (1, 2)), zorder=6)
    ax.axvline((sm - em) / 1000.0, color=INK2, lw=1.0, ls=(0, (3, 3)), zorder=4)
    lab = dict(fontsize=8.5, color=INK2, zorder=30, transform=ax.get_xaxis_transform(), bbox=dict(fc="#fcfcfb", ec="none", alpha=0.85, pad=1.5))
    ax.text((sm - em) / 1000.0, 0.985, "сигнал " + fmt_hms_ms(sm, em), ha="left", va="top", **lab)
    if touch_ms is not None:
        ax.axvline((touch_ms - em) / 1000.0, color=C_CROSS, lw=1.0, ls=(0, (1, 2)), zorder=4)
        ax.text((touch_ms - em) / 1000.0, 0.945, "первое касание уровня " + fmt_hms_ms(touch_ms, em), ha="left", va="top", **lab)
    acol = "#0a8f3c" if s > 0 else "#d03b3b"
    ax.plot([0], [e], marker="^" if s > 0 else "v", ms=12, mfc=acol, mec="white", mew=1.0, ls="none", zorder=28)
    ax.hlines(e, 0, (legs[-1]["ms"] - em) / 1000.0, color=acol, lw=1.5, zorder=24)
    if legs2:
        e2, ex2 = r["lot2"]["entry_px_e8"] / 1e8, (r["lot2"]["entry_ms"] - em) / 1000.0
        ax.plot([ex2], [e2], marker="^" if s > 0 else "v", ms=12, mfc="white", mec=acol, mew=1.8, ls="none", zorder=28)
        ax.hlines(e2, ex2, (legs2[-1]["ms"] - em) / 1000.0, color=acol, lw=1.5, ls=(0, (6, 2)), zorder=24)
    for lg in [legs] + ([legs2] if legs2 else []):
        for g in lg:
            gx = (g["ms"] - em) / 1000.0
            if g["kind"] == "take":
                ax.plot([gx], [g["px"]], marker="o", ms=13, mfc=C_TAKE, mec="white", mew=1.0, ls="none", zorder=28)
                ax.text(gx, g["px"], str(g["n"] + 1), ha="center", va="center", fontsize=6.5, fontweight="bold", color="white", zorder=29)
            else:
                ax.plot([gx], [g["px"]], marker="s", ms=12, mfc=KIND_COLOR[g["kind"]], mec="white", mew=1.0, ls="none", zorder=28)
                ax.text(gx, g["px"], "  " + KIND_SHORT[g["kind"]] + (" (лот 2)" if lg is not legs else ""), ha="left", va="bottom", fontsize=9, fontweight="bold",
                        color=KIND_COLOR[g["kind"]], zorder=30)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: fmt_hms(int(em + v * 1000), em)))
    price_axis(ax)
    ax.tick_params(labelsize=9)
    ax.set_xlabel("сделки ленты (светлая точка — покупатель-тейкер, тёмная — продавец-тейкер), время GMT+4", loc="left", fontsize=9)
    xr_px = LEFT + axw
    lo_, hi_ = float(BOT), float(BOT + axh)
    yd = [[ax.transData.transform((0, y))[1] for y in it["ys"]] for it in items]
    want = [(min(v) + max(v)) / 2 for v in yd]
    ys = dodge(want, lo_, hi_)
    tx_ = xr_px + GAP
    for it, v, y in zip(items, yd, ys):
        col = it["color"]
        for yy in v:
            fig.add_artist(Line2D([xr_px / W, (xr_px + 10) / W], [yy / H, yy / H], color=col, lw=0.8, alpha=0.6, transform=fig.transFigure))
        fig.add_artist(Line2D([q / W for q in (xr_px + 10, xr_px + GAP - 8, xr_px + GAP - 3)], [q / H for q in ((min(v) + max(v)) / 2, y, y)], color=col, lw=0.8, alpha=0.6,
                              transform=fig.transFigure))
        fig.text(tx_ / W, y / H, it["text"], fontsize=9.5, fontweight="bold" if it["bold"] else "normal", color=col, va="center", ha="left")
    day = datetime.fromtimestamp((em + GMT4) / 1000, tz=timezone.utc).strftime("%d.%m.%Y")
    title = (f"{r['sym']} · {SIDE_RU[r['side']]} · {day} · итог {r['net']:+.2f} $ ({r['net'] / NOTIONAL * 1e4:+.1f} бп) · {KIND_RU[r['kind']]}"
             + (" · 2 лота" if legs2 else "") + f" · держали {fmt_dur((xm - em) / 1000)} · секундный масштаб")
    fig.text(20 / W, 1 - 30 / H, title, fontsize=16, color=INK, fontweight="bold", va="center")
    fig.savefig(out_png, dpi=80)
    plt.close(fig)
    return W, H, int(LEFT + (0 - x0) / (x1 - x0) * axw), 0


def cmd_render(a):
    RULES.update(parse_rules(a.rules))
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
            tk = load_ticks(a.work, r["id"])
            W, H, ex, nt = render(r, kl, tk, os.path.join(a.out, f"{r['id']}.png"), plt)
            meta[r["id"]] = [W, H, ex, nt]
            W, H, ex, nt = render(r, kl, tk, os.path.join(a.out, f"{r['id']}_zoom.png"), plt, zoom=True)
            meta[r["id"] + "_zoom"] = [W, H, ex, nt]
            if (r["exit_ms"] - r["entry_ms"]) < 120_000:  # §13.19: удержание < 2 мин — секундный график
                W, H, ex, nt = render_secs(r, tk, os.path.join(a.out, f"{r['id']}_sec.png"), plt)
                meta[r["id"] + "_sec"] = [W, H, ex, nt]
            n += 1
            if n % 25 == 0:
                print(f"PNG {n}/{len(trades)}", flush=True)
    json.dump(meta, open(os.path.join(a.out, f"_meta_{a.tag}.json"), "w"))
    print(f"PNG готово: {n}")


# ───────────────────────────── индекс ─────────────────────────────

INDEX_HTML = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>П-42 — сделки: __NAME__ (тики 4 мс)</title>
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
.t-gone{border-color:#d03b3b;color:#a02222}.t-newm{border-color:#2a78d6;color:#1b5aa8}.t-cm{border-color:#c9c8c2;color:#6b6a66}
.t-tx{border-color:#7d6608;color:#7d6608}.t-rt{border-color:#a3478f;color:#a3478f}.t-stop{border-color:#d03b3b;color:#a02222}.t-be{border-color:#6250d6;color:#4a3aa7}.t-trail{border-color:#1baf7a;color:#0b7a55}.t-forced{border-color:#6b6a66;color:#6b6a66}
.fl{text-align:center;padding:5px 4px}.fl b{color:#c33}.flh{text-align:center}
#st{padding:6px 18px 10px;border-bottom:1px solid var(--line);font-size:13px}#st summary{cursor:pointer;color:var(--ink2)}
#st table{width:auto;margin-top:6px}#st td,#st th{position:static;cursor:default;padding:3px 9px;font-weight:400;white-space:normal;text-align:right}#st td.l,#st th.l{text-align:left}
#st tr.fr{cursor:pointer}#st tr.fr:hover{background:#eef4fc}#st a{color:var(--acc);cursor:pointer;margin-right:8px}
#modal{display:none;position:fixed;inset:0;background:rgba(15,15,15,.82);z-index:20}#modal.on{display:flex;flex-direction:column;align-items:center}
#modal .top{color:#eee;padding:8px 14px;font-size:13px;display:flex;gap:16px;align-items:center;width:100%}
#modal .top b{font-size:15px}#modal .top .sp{margin-left:auto}
#modal .top{background:#1b1b1b}#modal .imgw{flex:1;min-height:0;width:100%;overflow-y:auto;background:#262626}#modal .pn h4{margin:6px 14px 4px;color:#ddd;font-size:13px;font-weight:600}
#modal .sc{overflow-x:auto;text-align:center;margin-bottom:10px}#modal img{background:#fff;vertical-align:top}
#modal.fit img{max-width:100%;height:auto}#modal:not(.fit) img{max-width:none;max-height:none}
</style></head><body>
<header><h1>П-42 · пробой уровня · сделки: __NAME__ (тики 4 мс, 566 монет, 15.09–13.11)</h1>
<div class="sub">Одна сделка — одна строка. Клик по строке — два графика: сверху приближение (от 30 мин до сигнала до 30 мин после выхода, минутные свечи: ▲▼ вход, зелёные кружки — тейки, квадрат — выход, оранжевые × — переходы цены через уровень), ниже обзор (от первого пивота до выхода; широкую картинку листать мышью/колесом с Shift); ← → листают отфильтрованный список, Esc — закрыть, Z — вписать в окно / полный размер. Время — GMT+4; позиция $500; комиссии по тарифу владельца. Линия уровня и полоса ±τ — с рождения уровня (вертикальная пунктирная метка), пивоты — чёрные кружки. Вход — линия на цене входа (зелёная лонг / красная шорт), тейки — тонкие зелёные отрезки, трейл — бирюзовый, стоп — красный пунктир; подписи и касания — справа. __PKNOTE__</div></header>
<div class="bar" id="bar">
<label>Монета<input type="text" id="f_sym" placeholder="BTC…"></label>
<label>Сторона<select id="f_side"><option value="">все</option><option value="long">лонг</option><option value="short">шорт</option></select></label>
<label>Исход<select id="f_out"><option value="">все</option></select></label>
<label>Половина<select id="f_half"><option value="">обе</option><option>A</option><option>B</option></select></label>
<label>$ от<input type="number" id="f_min" step="any"></label><label>$ до<input type="number" id="f_max" step="any"></label>
<label>Дата с<input type="date" id="f_d1"></label><label>по<input type="date" id="f_d2"></label>
<label>Уровень<select id="f_cross"><option value="">не важно</option><option value="1">пересёк ≤30 мин</option><option value="0">не пересёк</option></select></label>
<label>Флаг уровня<select id="f_flag"><option value="">не важно</option><option value="any">любой</option><option value="none">нет флагов</option><option>A</option><option>B</option><option>C</option><option>D</option><option>E</option><option>F</option><option>G</option><option value="A_post">A после рождения</option></select></label>
<label>Новая<select id="f_new"><option value="">не важно</option><option value="1">новая (нет в старой b0)</option><option value="0">была в старой b0</option></select></label>
<label>Сигнал→вход ≥, с<input type="number" id="f_dly" step="any"></label>
<label>Метка<select id="f_mark"><option value="cur">сделки нового входа</option><option value="common">общие со старым</option><option value="new">появились</option><option value="gone">исчезли (были при старом входе)</option><option value="all">все (в т. ч. исчезли)</option></select></label>
<label>Допуск §13.14<select id="f_adm"><option value="">не важно</option><option value="a">только касание ≤ 4 ч</option><option value="b">проторговка (в т. ч. оба)</option><option value="both">оба условия</option><option value="none">ни одно</option></select></label>
<button id="reset">Сброс</button><span id="cnt"></span></div>
<details id="st" open><summary>Флаги уровня A–G — статистика (клик по строке — фильтр, клик по id — три примера)</summary><div id="stb"></div></details>
<table><thead><tr id="hdr"></tr></thead><tbody id="tb"></tbody></table>
<div id="modal"><div class="top"><b id="m_t"></b><span id="m_s"></span><span class="sp">← → · Esc · Z масштаб · <a id="m_az" style="color:#9cf" target="_blank">приближение</a> · <a id="m_a" style="color:#9cf" target="_blank">обзор</a></span></div><div class="imgw"><div class="pn" id="ps"><h4>Секундный график (удержание &lt; 2 мин): лента сделок, вход, выходы по виду</h4><div class="sc" id="ss"><img id="m_sc"></div></div><div class="pn" id="pz"><h4>Приближение: от 30 мин до сигнала до 30 мин после выхода</h4><div class="sc" id="sz"><img id="m_z"></div></div><div class="pn"><h4 id="po_h">Обзор: от первого пивота до выхода</h4><div class="sc" id="so"><img id="m_i"></div></div></div></div>
<script>
const D=__DATA__;const OLDNAME=__OLDNAME__;
const COLS=[["#","n",0],["Монета","sym",1],["Сторона","side_ru",1],["Вход (GMT+4)","entry_gmt4",1],["Исход","outcome",1],["Вид выхода","exit_kind_ru",1],["Лот 1: вид, $","lot1_txt",1],["Лот 2: вход, цена → вид, $","lot2_txt",1],["Тейков","takes",0],["$ итог","net_usd",0],["бп итог","net_bps",0],
["Держали","hold_s",0],["Сигнал→вход, с","sig_to_fill_s",0],["Прежний вход (GMT+4)","old_entry_gmt4",1],["Цена прежнего","old_entry_px",0],["Цена нового","entry_px",0],["Δ цены, бп","entry_diff_bp",0],["Метка","mark",1],["NATR %","natr_pct",0],["До уровня, бп","dist_entry_bps",0],["Возраст ур., ч","age_h",0],["Пивотов","n_pivots",0],["Касаний","touches",0],["разброс отскоков, bp","pivot_spread_bps",0],["касание до входа, мин","last_touch_age_min",0],["MFE 30м, бп","mfe_30m_bps",0],["MAE 30м, бп","mae_30m_bps",0],["Пересёк","crossed",1],
["Новая","is_new",2],["Допуск","adm",1],["A","flag_A",2],["B","flag_B",2],["C","flag_C",2],["D","flag_D",2],["E","flag_E",2],["F","flag_F",2],["G","flag_G",2]];
const FT=__FLAGTXT__,ST=__FLAGS__;
const TIPS={pivot_spread_bps:"разброс цен пивотов (отскоков) уровня: (макс − мин) / среднее, бп; уровень — среднее пивотов, допуск каждого τ = 20 бп от среднего, для двух пивотов разброс до 40 бп",last_touch_age_min:"минут от последнего касания уровня до минуты входа; касание — минута, где high (лонг) / low (шорт) дошёл до нижней (верхней) границы полосы ±τ; считаются и пивоты, и подход цены к уровню перед сигналом"};
const OUTR={stop:"стоп","be":"безубыток",trail:"трейл",forced:"принуд.",tx:"время",rt:"возврат"};
D.forEach((d,i)=>{d.lot1_txt=d.lot2?d.lot1_kind+" "+d.lot1_net.toFixed(2):"—";d.lot2_txt=d.lot2?d.lot2_entry_gmt4.slice(11,23)+" · "+String(+(+d.lot2_entry_px).toPrecision(6))+" → "+d.lot2_kind+" "+d.lot2_net.toFixed(2):"—";d.n=i+1;d.side_ru=d.side==="long"?"лонг":"шорт";d.age_h=d.level_age_min/60;d.crossed=d.crossed_30m?"да":"нет";d.date=d.entry_gmt4.slice(0,10)});
const fmtDur=s=>{s=Math.round(s);if(s<60)return s+" с";if(s<3600)return Math.floor(s/60)+" мин "+String(s%60).padStart(2,"0")+" с";const h=Math.floor(s/3600);if(h<48)return h+" ч "+String(Math.floor(s%3600/60)).padStart(2,"0")+" мин";return Math.floor(h/24)+" сут. "+(h%24)+" ч"};
const outs=[...new Set(D.map(d=>d.exit_kind))];const so=document.getElementById("f_out");outs.forEach(o=>{const e=document.createElement("option");e.value=o;e.textContent=OUTR[o]||o;so.appendChild(e)});
let sk="entry_gmt4",sd=1,view=[],nav=[],cur=-1;
const hdr=document.getElementById("hdr");COLS.forEach(([t,k,l])=>{const th=document.createElement("th");th.className=l===1?"l":l===2?"flh":"";if(TIPS[k])th.title=TIPS[k];if(l===2)th.title=k==="adm"?"какое условие §13.14 допускает сделку: касание уровня не старше 4 ч (а) и/или проторговка 120 мин (б); «нет» — правило не применялось (набор без §13.14)":k==="is_new"?"новая: сделки нет в прежней базе b0 (появилась под pk=1: освободился слот или не наступила пауза)":FT[k.slice(5)];th.innerHTML=t+' <span class="ar"></span>';th.onclick=()=>{if(sk===k)sd=-sd;else{sk=k;sd=1}render()};th.dataset.k=k;hdr.appendChild(th)});
const $=id=>document.getElementById(id);
function filt(){const q=$("f_sym").value.trim().toUpperCase(),sd_=$("f_side").value,o=$("f_out").value,h=$("f_half").value,mn=$("f_min").value,mx=$("f_max").value,d1=$("f_d1").value,d2=$("f_d2").value,c=$("f_cross").value,fg=$("f_flag").value,nw=$("f_new").value,dl=$("f_dly").value,am=$("f_adm").value,mk=$("f_mark").value;
return D.filter(d=>(mk==="all"||(mk==="gone"?d.mark==="исчезла":mk==="common"?d.mark==="общая":mk==="new"?d.mark==="появилась":d.mark!=="исчезла"))&&(!fg||(fg==="any"?d.n_flags>0:fg==="none"?d.n_flags===0:d["flag_"+fg]))&&(!q||d.sym.includes(q))&&(!sd_||d.side===sd_)&&(!o||d.exit_kind===o)&&(!h||d.half===h)&&(mn===""||d.net_usd>=+mn)&&(mx===""||d.net_usd<=+mx)&&(!d1||d.date>=d1)&&(!d2||d.date<=d2)&&(c===""||(c==="1")===d.crossed_30m)&&(nw===""||(nw==="1")===d.is_new)&&(dl===""||d.sig_to_fill_s>=+dl)&&(am===""||(am==="a"&&d.adm==="касание")||(am==="b"&&d.adm_cons)||(am==="both"&&d.adm==="оба")||(am==="none"&&d.adm==="нет")))}
function cell(d,k){const v=d[k];if(k.startsWith("flag_")||k==="is_new")return v?"<b>●</b>":"";switch(k){case"net_usd":return `<span class="${v>=0?"pos":"neg"}">${v.toFixed(2)}</span>`;case"net_bps":return `<span class="${v>=0?"pos":"neg"}">${v.toFixed(1)}</span>`;
case"outcome":return `<span class="tag t-${d.exit_kind}">${OUTR[d.exit_kind]||d.exit_kind}${d.takes>0&&d.exit_kind!=="forced"?" + тейки":""}</span>`;case"hold_s":return fmtDur(v);case"sig_to_fill_s":return v==null?"—":v.toFixed(3);case"entry_gmt4":return v.slice(8,10)+"."+v.slice(5,7)+" "+v.slice(11,23);
case"old_entry_gmt4":return v==null?"—":v.slice(8,10)+"."+v.slice(5,7)+" "+v.slice(11,23);case"old_entry_px":case"entry_px":return v==null||(k==="entry_px"&&d.mark==="исчезла")?"—":String(+(+v).toPrecision(6));
case"entry_diff_bp":return v==null?"—":`<span class="${v>0?"neg":v<0?"pos":""}">${v.toFixed(2)}</span>`;case"mark":return v?`<span class="tag t-${v==="исчезла"?"gone":v==="появилась"?"newm":"cm"}" title="${d.mark_why||""}">${v}</span>`:"";
case"natr_pct":case"age_h":return v.toFixed(2);case"dist_entry_bps":case"pivot_spread_bps":case"mfe_30m_bps":case"mae_30m_bps":return v==null?"—":v.toFixed(1);default:return v==null?"—":v}}
function render(){view=filt();view.sort((a,b)=>{const x=a[sk],y=b[sk];return (x<y?-1:x>y?1:0)*sd||a.n-b.n});
document.querySelectorAll("#hdr th").forEach(th=>th.querySelector(".ar").textContent=th.dataset.k===sk?(sd>0?"▲":"▼"):"");
$("tb").innerHTML=view.map((d,i)=>"<tr data-i='"+i+"'>"+COLS.map(([t,k,l])=>`<td class="${l===1?"l":l===2?"fl":""}">${cell(d,k)}</td>`).join("")+"</tr>").join("");
const s=view.reduce((a,d)=>a+d.net_usd,0);$("cnt").textContent=`${view.length} из ${D.length} · сумма ${s.toFixed(2)} $ · средняя ${view.length?(s/view.length).toFixed(2):"—"} $`;}
const byId=Object.fromEntries(D.map(d=>[d.id,d]));
(function(){const rows=[...("ABCDEFG".split("")).map(c=>[c,c+" — "+FT[c]]),["A_post","A после рождения уровня (подмножество A)"],...(ST.new?[["new","новые сделки (нет в старой b0)"]]:[]),["slow","сигнал→вход ≥ 1 с (долгое исполнение входа)"],...(ST.adm_a?[["adm_a","допуск: только касание уровня ≤ 4 ч (§13.14 а)"],["adm_b","допуск: проторговка 120 мин (§13.14 б), в т. ч. вместе с касанием"]]:[]),["any","любой флаг"],["none","без флагов"],["all","все сделки"]];
let h="<table><thead><tr><th class='l'>Флаг</th><th>сделок</th><th>лонг</th><th>шорт</th><th>сумма $</th><th>средняя $</th><th>прибыльных</th><th class='l'>примеры (id · значение метрики · $)</th></tr></thead><tbody>";
rows.forEach(([k,t])=>{const s=ST[k];h+=`<tr class="fr" data-k="${k}"><td class="l">${t}</td><td>${s.n}</td><td>${s.n_long}</td><td>${s.n_short}</td><td class="${s.sum_usd>=0?"pos":"neg"}">${s.sum_usd.toFixed(2)}</td><td>${s.mean_usd==null?"—":s.mean_usd.toFixed(3)}</td><td>${s.win==null?"—":(s.win*100).toFixed(1)+" %"}</td><td class="l">${(s.examples||[]).map((e,j)=>`<a data-k="${k}" data-j="${j}">${e.id}</a>${e.v} · ${e.net>=0?"+":""}${e.net}`).join(" &nbsp; ")}</td></tr>`});
$("stb").innerHTML=h+"</tbody></table>";
$("stb").onclick=e=>{const a=e.target.closest("a");if(a){e.stopPropagation();nav=ST[a.dataset.k].examples.map(x=>byId[x.id]);open(+a.dataset.j);return}
const tr=e.target.closest("tr.fr");if(tr){const k=tr.dataset.k;$("f_flag").value=["A","B","C","D","E","F","G","A_post","any","none"].includes(k)?k:"";$("f_new").value=k==="new"?"1":"";$("f_dly").value=k==="slow"?"1":"";$("f_adm").value=({adm_a:"a",adm_b:"b",adm_none:"none"})[k]||"";render()}}})();
$("tb").onclick=e=>{const tr=e.target.closest("tr");if(tr){nav=view;open(+tr.dataset.i)}};
function flagLine(d){if(!d.n_flags)return " · флагов уровня нет";const t=[];if(d.flag_A)t.push(`A прокол ${d.pierce_over_bps.toFixed(1)} бп, закрытий за уровнем ${d.pierce_close_n}`+(d.flag_A_post?` (после рождения: ${d.pierce_over_bps_post.toFixed(1)} бп, ${d.pierce_close_n_post})`:" (только до рождения уровня)"));if(d.flag_B)t.push(`B пивоты ${d.min_pivot_gap_min} мин друг от друга`);if(d.flag_C)t.push(`C уровень известен ${d.level_known_min} мин`);if(d.flag_D)t.push(`D диапазон ${d.spike_range_natr.toFixed(1)} NATR`);if(d.flag_E)t.push(`E разброс ${d.pivot_spread_bps.toFixed(1)} бп`+(d.level_vs_mean_e8?`, цена ≠ среднему на ${d.level_vs_mean_e8} e8`:""));if(d.flag_F)t.push(`F пивот не экстремум (${d.pivot_not_extreme_n})`);if(d.flag_G)t.push(`G закрытий за уровнем ${d.late_close_beyond_n}, до ${d.late_close_over_bps.toFixed(1)} бп`);return " · флаги: "+t.join("; ")}
function open(i){if(i<0||i>=nav.length)return;cur=i;const d=nav[i];const im=$("m_i"),so=$("so"),iz=$("m_z"),sz=$("sz");document.querySelector(".imgw").scrollTop=0;im.onload=()=>{so.scrollLeft=Math.max(0,(d.ex||0)-so.clientWidth*0.35)};const src=d.img||("charts/"+d.id+".png");im.src=src;$("m_a").href=src;
if(d.sec){$("ps").style.display="";const ic=$("m_sc");ic.src=d.sec}else{$("ps").style.display="none";$("m_sc").removeAttribute("src")}
if(d.zoom){$("pz").style.display="";iz.onload=()=>{sz.scrollLeft=Math.max(0,(d.zex||0)-sz.clientWidth*0.35)};iz.src=d.zoom;$("m_az").href=d.zoom;$("m_az").style.display=""}else{$("pz").style.display="none";iz.removeAttribute("src");$("m_az").style.display="none"}
$("po_h").textContent=d.mark==="исчезла"?"График прежнего набора ("+OLDNAME+"): от первого пивота до выхода":"Обзор: от первого пивота до выхода";$("m_t").textContent=`${d.sym} ${d.side_ru} ${d.entry_gmt4.slice(0,19)}`;
$("m_s").textContent=`${d.net_usd.toFixed(2)} $ · ${d.net_bps.toFixed(1)} бп · ${OUTR[d.exit_kind]} · держали ${fmtDur(d.hold_s)} · касаний ${d.touches} · ${i+1}/${nav.length}`+(d.sig_to_fill_s!=null?` · сигнал→вход ${d.sig_to_fill_s.toFixed(3)} с`:d.old_sig_to_fill_s!=null?` · прежний вход: сигнал→вход ${(+d.old_sig_to_fill_s).toFixed(3)} с`:"")+flagLine(d)+(d.mark==="исчезла"?" · ИСЧЕЗЛА при новом входе ("+d.mark_why+"); график прежнего входа":d.mark==="появилась"?" · появилась (нет при старом входе)":"")+(d.entry_diff_bp!=null?` · цена входа новая − прежняя ${d.entry_diff_bp.toFixed(2)} бп`:"");$("modal").classList.add("on")}
document.addEventListener("keydown",e=>{if(!$("modal").classList.contains("on"))return;if(e.key==="Escape")$("modal").classList.remove("on");else if(e.key==="ArrowRight")open(cur+1);else if(e.key==="ArrowLeft")open(cur-1);else if(e.key==="z"||e.key==="Z")$("modal").classList.toggle("fit")});
$("modal").onclick=e=>{if(e.target.id==="modal"||e.target.classList.contains("imgw")||e.target.classList.contains("sc"))$("modal").classList.remove("on")};
document.querySelectorAll(".bar input,.bar select").forEach(el=>el.oninput=render);$("reset").onclick=()=>{document.querySelectorAll(".bar input,.bar select").forEach(el=>el.value="");render()};
document.documentElement.style.setProperty("--barh",document.getElementById("bar").offsetHeight+"px");render();
</script></body></html>
"""


SLOW_S = 1.0  # «долгое исполнение входа»: сигнал→вход от этого числа секунд


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
    if rows and rows[0].get("is_new") is not None:
        sel = [x for x in rows if x["is_new"]]
        d = _agg(sel)
        d["metric"] = "net_usd"
        d["examples"] = [dict(id=x["id"], v=round(x["net_usd"], 2), net=round(x["net_usd"], 2))
                         for x in sorted(sel, key=lambda x: (-abs(x["net_usd"]), x["id"]))[:3]]
        out["new"] = d
    sel = [x for x in rows if x["sig_to_fill_s"] >= SLOW_S]
    d = _agg(sel)
    d["metric"] = "sig_to_fill_s"
    d["examples"] = [dict(id=x["id"], v=x["sig_to_fill_s"], net=round(x["net_usd"], 2))
                     for x in sorted(sel, key=lambda x: (-x["sig_to_fill_s"], x["id"]))[:3]]
    out["slow"] = d
    for key, sel in (("adm_a", [x for x in rows if x["adm"] == "касание"]), ("adm_b", [x for x in rows if x["adm"] in ("проторговка", "оба")]),
                     ("adm_none", [x for x in rows if x["adm"] == "нет"])):
        out[key] = _agg(sel)
        out[key]["metric"] = "net_usd"
        out[key]["examples"] = [dict(id=x["id"], v=round(x["net_usd"], 2), net=round(x["net_usd"], 2))
                                for x in sorted(sel, key=lambda x: (-abs(x["net_usd"]), x["id"]))[:3]]
    return out


def flagstats_md(st):
    L = ["| флаг | сделок | лонг | шорт | сумма $ | средняя $ | прибыльных | метрика | примеры (id: значение, $) |", "|---|---:|---:|---:|---:|---:|---:|---|---|"]
    for key, name in [(c, f"{c} — {FLAG_TXT[c]}") for c in list(FLAGS) + ["A_post"]] + [(k, n) for k, n in (("new", "новые сделки (нет в старой b0)"), ("slow", f"сигнал→вход ≥ {SLOW_S:g} с"), ("adm_a", "допуск: только касание ≤ 4 ч"),
                                                              ("adm_b", "допуск: проторговка (в т. ч. вместе с касанием)"),
                                                              ("adm_none", "допуск: ни (а), ни (б) — правило не применялось")) if k in st] \
            + [("any", "любой флаг"), ("none", "без флагов"), ("all", "все сделки")]:
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
            "flags", "n_flags", "sig_to_fill_s", "is_new", "adm", "adm_touch", "adm_cons", "cons_minutes", "pierce_over_bps", "pierce_close_n", "min_pivot_gap_min", "level_known_min", "spike_range_natr",
            "pierce_over_bps_post", "pierce_close_n_post", "flag_A_post", "pivot_spread_bps", "last_touch_age_min", "spread_gt_2tau", "level_vs_mean_e8", "pivot_not_extreme_n", "late_close_beyond_n", "late_close_over_bps"] + ["flag_" + c for c in FLAGS]
    meta = {}
    cdir = a.charts or os.path.join(a.pub, "charts")
    for fn in os.listdir(cdir) if os.path.isdir(cdir) else []:
        if fn.startswith("_meta_"):
            meta.update(json.load(open(os.path.join(cdir, fn))))
    extra = ["exit_kind_ru", "lot2", "lot1_kind", "lot1_net", "lot2_entry_gmt4", "lot2_entry_px", "lot2_kind", "lot2_net", "rules", "entry_px", "entry_model", "old_entry_gmt4", "old_entry_px", "entry_diff_bp", "old_sig_to_fill_s", "old_entry_beyond_level", "entry_beyond_level"]
    data = [{**{k: r[k] for k in keep}, **{k: r.get(k) for k in extra}} for r in rows]
    for d, r in zip(data, rows):
        d["mark"] = ("появилась" if r["is_new"] else "общая") if r.get("is_new") is not None else ""
    pknote = ""
    if a.rules:
        pknote = "Правила набора (ключи варианта §13.19): " + a.rules.replace(";", "; ") + ". "
    if a.old:  # прежний набор: «появилась» — нет среди сделок прежнего набора; «исчезла» — сделка прежнего набора, которой нет в новом (ключ — монета, сторона, время сигнала)
        new_keys = {(r["sym"], r["side"], r["sig_ms"]) for r in load_trades(a.detail)}
        old_tr = load_trades(a.old)
        old_keys = {(r["sym"], r["side"], r["sig_ms"]) for r in old_tr}
        pknote += (f"Набор = {a.name}: {len(new_keys)} сделок; из прежних {len(old_keys)} ({a.oldname}) исчезло {len(old_keys - new_keys)}, "
                  f"появилось новых {len(new_keys - old_keys)}.")
        if a.oldfeat:  # исчезнувшие — строки с графиком прежнего набора (подпапка), причина — из <сторона>-entryrej.csv прогона
            rj = {}
            for side in ("long", "short"):
                p = os.path.join(a.rej, f"{side}-entryrej.csv") if a.rej else ""
                if p and os.path.exists(p):
                    for x in csv.DictReader(open(p, encoding="utf-8")):
                        rj[(x["sym"], side, int(x["sig_ms"]) + T0)] = x["why"]
            why_ru = {"beyond": "отвергнута: цена уже за уровнем", "stale": "отвергнута: нет сделки стороны не старше W мс"}
            oldfeat = {x["id"]: x for x in (json.loads(l) for l in open(a.oldfeat, encoding="utf-8"))}
            cnt = defaultdict(int)
            for r in old_tr:
                k = (r["sym"], r["side"], r["sig_ms"])
                if k in new_keys:
                    continue
                f = oldfeat[r["id"]]
                reason = why_ru.get(rj.get(k), "сигнал не дошёл до входа: слот, пауза или монета заняты (портфель изменился)")
                cnt[reason] += 1
                row = {k2: f.get(k2) for k2 in keep}
                row.update(id="X_" + r["id"], img=f"{a.oldcharts}/{r['id']}.png", mark="исчезла", mark_why=reason, ex=0, w=0,
                           old_entry_gmt4=f["entry_gmt4"], old_entry_px=f["entry_px"], entry_px=None, entry_diff_bp=None, sig_to_fill_s=None,
                           old_sig_to_fill_s=f["sig_to_fill_s"], entry_model="следующая сделка")
                data.append(row)
            pknote += " Исчезли: " + "; ".join(f"{n} — {w}" for w, n in sorted(cnt.items())) + "."
    for d in data:
        if d.get("mark") == "исчезла":
            continue
        m = meta.get(d["id"])
        d["ex"], d["w"] = (m[2], m[0]) if m else (0, 0)
        z = meta.get(d["id"] + "_zoom")
        if z:  # приближение: charts/<id>_zoom.png; zex — x входа в пикселях картинки (по нему окно прокручивается)
            d["zoom"], d["zex"] = f"charts/{d['id']}_zoom.png", z[2]
        if meta.get(d["id"] + "_sec"):  # §13.19: секундный график (удержание < 2 мин)
            d["sec"] = f"charts/{d['id']}_sec.png"
    for d in data:
        for k, v in d.items():
            if isinstance(v, float) and k not in ("entry_px", "old_entry_px"):
                d[k] = round(v, 4)
    os.makedirs(a.pub, exist_ok=True)
    st = flagstats(rows)
    json.dump(st, open(os.path.join(a.work, "flagstats.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(os.path.join(a.work, "flagstats.md"), "w", encoding="utf-8").write(flagstats_md(st))
    html = INDEX_HTML.replace("__DATA__", json.dumps(data, ensure_ascii=False)).replace("__FLAGS__", json.dumps(st, ensure_ascii=False)) \
        .replace("__FLAGTXT__", json.dumps(FLAG_TXT, ensure_ascii=False)).replace("__OLDNAME__", json.dumps(a.oldname, ensure_ascii=False)).replace("__PKNOTE__", pknote).replace("__NAME__", a.name)
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
    p.add_argument("--old", help="каталог с b0_lx4/<сторона>-detail.jsonl прежней базы b0 (для признака is_new)")
    p.add_argument("--rules", default="", help="§13.19: ключи варианта по сторонам, «long:tt=20 tb=0.3;short:bm=1 e2=0.05» — линии правил и подписи")
    p = sub.add_parser("render")
    p.add_argument("detail"); p.add_argument("work"); p.add_argument("--only"); p.add_argument("--out", required=True); p.add_argument("--tag", default="all")
    p.add_argument("--rules", default="", help="§13.19: ключи варианта по сторонам, «long:tt=20 tb=0.3;short:bm=1 e2=0.05»")
    p = sub.add_parser("index")
    p.add_argument("work"); p.add_argument("pub"); p.add_argument("--charts")
    p.add_argument("--detail", default="out"); p.add_argument("--name", default="b0 + pk=1", help="название набора в заголовке"); p.add_argument("--old", help="каталог с b0_lx4/<сторона>-detail.jsonl прежней базы b0")
    p.add_argument("--oldfeat", help="features.jsonl прежнего набора — по нему строки исчезнувших сделок (§13.16)")
    p.add_argument("--oldcharts", default="старый вход 15.11/charts", help="путь к графикам прежнего набора относительно index.html")
    p.add_argument("--oldname", default="вход «следующая сделка», 15.11", help="как называть прежний набор (--old) в шапке и подсказках")
    p.add_argument("--rej", help="каталог клетки нового входа с <сторона>-entryrej.csv — причина «исчезла»")
    p.add_argument("--rules", default="", help="§13.19: ключи варианта по сторонам — строка в шапке индекса")
    p = sub.add_parser("flagstats")
    p.add_argument("work")
    a = ap.parse_args()
    {"features": cmd_features, "render": cmd_render, "index": cmd_index, "flagstats": cmd_flagstats}[a.cmd](a)


if __name__ == "__main__":
    main()
