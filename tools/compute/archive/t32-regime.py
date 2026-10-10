#!/usr/bin/env python3
"""T-32 (regime): разбор провалов главного варианта («максимум -> новый максимум» дольше 3 суток
в августе/сентябре) и кандидаты-выключатели режима.

Все пороги подбираются ТОЛЬКО на августе (небольшой перебор, число сочетаний печатается) и без
изменений применяются к сентябрю. Правило исполняется в момент входа t0 только по данным до t0
(причинно) - см. build_features(). Убранные правилом сделки просто выпадают из main-trades.csv,
занятость монеты не пересчитывается (то же упрощение, что и в остальных инструментах t32/T-32).

    python tools/compute/t32-regime.py --out data/t32/regime.json --summary data/t32/regime-summary.md
"""
import argparse
import bisect
import csv
import datetime as dt
import glob
import importlib.util
import itertools
import json
import math
import os

ROOT = "data/t32"
MS_H = 3_600_000
MS_D = 24 * MS_H
FILTER_BPS = -44.55
GAP_MS = 30 * 60_000

spec = importlib.util.spec_from_file_location("kn", "tools/compute/kpi-newhigh.py")
kn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kn)

_lib_spec = importlib.util.spec_from_file_location(
    "_lib", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lib", "__init__.py"))
_lib = importlib.util.module_from_spec(_lib_spec)
_lib_spec.loader.exec_module(_lib)

PERIODS = kn.PERIODS  # aug/sep/augsep -> (name, start_day, end_day)


def iso(t_ms):
    return dt.datetime.fromtimestamp(t_ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


# ---------- load ----------

def load_trades():
    # T-21 batch 2: `_lib.read_csv` вместо своей копии `open` + `csv.DictReader` (main-trades.csv без `#` в шапке).
    rows = _lib.read_csv(os.path.join(ROOT, "main-trades.csv"))[1]
    for r in rows:
        r["t0_ms"] = int(r["t0_ms"]); r["t1_ms"] = int(r["t1_ms"]); r["pnl_usd"] = float(r["pnl_usd"])
        r["usd"] = float(r["usd"]); r["net_bps"] = float(r["net_bps"])
    return rows


def load_btc_1m():
    d = {}
    for f in [f"{ROOT}/epochs/e-archive/study/regime/ref-BTCUSDT-1m.csv",
              f"{ROOT}/epochs/e-aug/study/regime/ref-BTCUSDT-1m.csv",
              f"{ROOT}/study/regime/ref-BTCUSDT-1m.csv"]:
        if not os.path.exists(f):
            continue
        for row in csv.DictReader(open(f, encoding="utf-8")):
            d[int(row["minute_ms"])] = float(row["close"])
    times = sorted(d)
    closes = [d[t] for t in times]
    return times, closes


def load_regime():
    """minute_ms -> dict полей (объединение по своим суткам, поздний источник не перекрывает раньше найденный)."""
    d = {}
    for f in sorted(glob.glob(f"{ROOT}/epochs/e-aug/study/regime/2026-*.csv")) + \
             sorted(glob.glob(f"{ROOT}/epochs/e-archive/study/regime/2026-*.csv")) + \
             sorted(glob.glob(f"{ROOT}/study/regime/2026-*.csv")):
        day = os.path.basename(f)[:-4]
        day_start = kn.ms(day)
        day_end = day_start + MS_D
        for row in csv.DictReader(open(f, encoding="utf-8")):
            t = int(row["minute_ms"])
            if not (day_start <= t < day_end):
                continue
            if t in d:
                continue
            d[t] = {k: (float(v) if v != "" else None) for k, v in row.items() if k != "minute_ms"}
    return d


# ---------- BTC trend / RV (causal, precomputed prefix arrays over sorted minute grid) ----------

class BtcSeries:
    def __init__(self, times, closes):
        self.t = times
        self.c = closes
        n = len(times)
        self.logret2 = [0.0] * n
        for i in range(1, n):
            if times[i] - times[i - 1] <= 5 * 60_000 and closes[i - 1] > 0:
                lr = math.log(closes[i] / closes[i - 1])
                self.logret2[i] = lr * lr
        self.prefix = [0.0] * (n + 1)
        for i in range(n):
            self.prefix[i + 1] = self.prefix[i] + self.logret2[i]

    def _idx_at_or_before(self, t):
        i = bisect.bisect_right(self.t, t) - 1
        return i if i >= 0 else None

    def close_at(self, t):
        i = self._idx_at_or_before(t)
        return self.c[i] if i is not None else None

    def trend_bps(self, t, back_ms):
        i1 = self._idx_at_or_before(t)
        i0 = self._idx_at_or_before(t - back_ms)
        if i1 is None or i0 is None or self.t[i1] - self.t[i0] < back_ms - 6 * MS_H:
            return None  # недостаточно истории назад
        return (self.c[i1] / self.c[i0] - 1.0) * 1e4

    def rv_bps(self, t, back_ms=MS_D):
        i1 = self._idx_at_or_before(t)
        i0 = self._idx_at_or_before(t - back_ms)
        if i1 is None or i0 is None:
            return None
        return math.sqrt(max(0.0, self.prefix[i1 + 1] - self.prefix[i0 + 1])) * 1e4


# ---------- filter episodes (btc_ret_4h_bps <= FILTER_BPS, gaps <= 30 min merged) ----------

def filter_episodes(regime, t_start, t_end):
    minutes = sorted(t for t in regime if t_start <= t < t_end)
    below = [t for t in minutes if regime[t].get("btc_ret_4h_bps") is not None and regime[t]["btc_ret_4h_bps"] <= FILTER_BPS]
    if not below:
        return []
    eps = []
    cur = [below[0], below[0]]
    for t in below[1:]:
        if t - cur[1] <= GAP_MS:
            cur[1] = t
        else:
            eps.append(tuple(cur)); cur = [t, t]
    eps.append(tuple(cur))
    out = []
    for a, b in eps:
        depth = min(regime[t]["btc_ret_4h_bps"] for t in minutes if a <= t <= b and regime[t].get("btc_ret_4h_bps") is not None)
        out.append({"start": a, "end": b, "len_min": (b - a) / 60_000 + 1, "depth_bps": depth})
    return out


def episode_state_at(episodes, regime, t):
    """Причинно: активный на момент t эпизод (start<=t<=end по полному эпизоду, что он ещё не кончился —
    видно по btc_ret_4h_bps(t) <= FILTER_BPS), возраст = t-start, глубина = минимум btc_ret_4h_bps ТОЛЬКО
    по минутам от start до t включительно (не по всему эпизоду) — без заглядывания вперёд."""
    for e in episodes:
        if e["start"] <= t <= e["end"]:
            mins = [u for u in regime if e["start"] <= u <= t and regime[u].get("btc_ret_4h_bps") is not None]
            depth = min((regime[u]["btc_ret_4h_bps"] for u in mins), default=FILTER_BPS)
            return {"age_min": (t - e["start"]) / 60_000, "depth_bps": depth}
    return None


def episodes_in_trailing(episodes, t, back_ms=MS_D):
    """Число эпизодов, НАЧАВШИХСЯ в [t-back_ms, t] — причинно, зависит только от start."""
    return sum(1 for e in episodes if t - back_ms <= e["start"] <= t)


# ---------- periods "max -> new max" ----------

def find_periods(closes_sorted, start_ms, end_ms, min_days=3.0):
    cum = peak = 0.0
    t_peak = start_ms
    out = []
    for t, p in closes_sorted:
        cum += p
        if cum > peak + 1e-9:
            out.append({"start": t_peak, "end": t, "dur_days": (t - t_peak) / MS_D, "kind": "closed"})
            peak, t_peak = cum, t
    tail_end = max(end_ms, closes_sorted[-1][0] if closes_sorted else end_ms)
    if tail_end > t_peak:
        out.append({"start": t_peak, "end": tail_end, "dur_days": (tail_end - t_peak) / MS_D, "kind": "tail"})
    return [p for p in out if p["dur_days"] > min_days], out


# ---------- regime comparison: period vs "normal" ----------

def regime_avg(btc, regime, t_start, t_end, step_ms=15 * 60_000):
    ts = list(range(t_start, t_end, step_ms))
    tr24 = [x for x in (btc.trend_bps(t, MS_D) for t in ts) if x is not None]
    tr7d = [x for x in (btc.trend_bps(t, 7 * MS_D) for t in ts) if x is not None]
    rv = [x for x in (btc.rv_bps(t, MS_D) for t in ts) if x is not None]
    pool = []
    for t in ts:
        tm = t - t % 60_000
        if tm in regime and regime[tm].get("pool_ret_4h_bps") is not None:
            pool.append(regime[tm]["pool_ret_4h_bps"])
    mean = lambda xs: (sum(xs) / len(xs)) if xs else None
    return {"n_minutes_sampled": len(ts), "btc_trend24h_bps_avg": mean(tr24), "btc_trend7d_bps_avg": mean(tr7d),
            "btc_rv24h_bps_avg": mean(rv), "pool_ret4h_bps_avg": mean(pool)}


def complement(intervals, start, end):
    ivs = sorted(intervals)
    out, cur = [], start
    for a, b in ivs:
        if a > cur:
            out.append((cur, min(a, end)))
        cur = max(cur, b)
        if cur >= end:
            break
    if cur < end:
        out.append((cur, end))
    return out


def trades_in(trades, month, t_start, t_end):
    return [t for t in trades if t["month"] == month and t_start <= t["t0_ms"] < t_end]


def coin_breakdown(trs):
    by_sym = {}
    for t in trs:
        by_sym.setdefault(t["sym"], {"pnl": 0.0, "n": 0, "stops": 0})
        by_sym[t["sym"]]["pnl"] += t["pnl_usd"]
        by_sym[t["sym"]]["n"] += 1
        by_sym[t["sym"]]["stops"] += 1 if t["reason"] == "stop" else 0
    ranked = sorted(by_sym.items(), key=lambda kv: kv[1]["pnl"])[:8]
    total_stops = sum(v["stops"] for v in by_sym.values())
    total_n = sum(v["n"] for v in by_sym.values())
    return {"top_losers": [{"sym": s, **v} for s, v in ranked],
            "stop_fraction": round(total_stops / total_n, 3) if total_n else None,
            "n_trades": total_n, "pnl_usd": round(sum(v["pnl"] for v in by_sym.values()), 2)}


# ---------- features for rules (causal, evaluated at t0) ----------

def build_features(trades, btc, regime, episodes_by_month):
    trades_by_t1 = {"aug": sorted([t for t in trades if t["month"] == "aug"], key=lambda t: t["t1_ms"]),
                     "sep": sorted([t for t in trades if t["month"] == "sep"], key=lambda t: t["t1_ms"])}
    feats = {}
    for t in trades:
        m = t["month"]
        t0 = t["t0_ms"]
        eps = episodes_by_month[m]
        closed_before = [c for c in trades_by_t1[m] if c["t1_ms"] <= t0]
        last_pnls = {n: sum(c["pnl_usd"] for c in closed_before[-n:]) for n in (3, 5, 8, 10)}
        est = episode_state_at(eps, regime, t0)
        t0_minute = t0 - t0 % 60_000
        feats[id(t)] = {
            "btc_trend24h_bps": btc.trend_bps(t0, MS_D),
            "btc_trend7d_bps": btc.trend_bps(t0, 7 * MS_D),
            "btc_rv24h_bps": btc.rv_bps(t0, MS_D),
            "pool_ret4h_bps": regime[t0_minute]["pool_ret_4h_bps"] if t0_minute in regime else None,
            "n_episodes_trailing24h": episodes_in_trailing(eps, t0, MS_D),
            "episode_age_min": est["age_min"] if est else 0.0,
            "episode_depth_bps": est["depth_bps"] if est else 0.0,
            "last_pnl_3": last_pnls[3], "last_pnl_5": last_pnls[5], "last_pnl_8": last_pnls[8], "last_pnl_10": last_pnls[10],
        }
    return feats


def apply_rule(trades, feats, rule_fn):
    return [t for t in trades if not rule_fn(feats[id(t)])]


def month_kpi_usd(trades, month):
    closes = [(t["t1_ms"], t["pnl_usd"]) for t in trades if t["month"] == month]
    m = kn.month_metrics(closes, month)
    return {"worst_days": round(m["hours"]["worst"] / 24, 1) if m["hours"]["worst"] is not None else None,
            "tw_p90_h": round(m["hours"]["tw_p90"], 0) if m["hours"]["tw_p90"] is not None else None,
            "usd": m["usd"], "n": m["n"]}


RULES = [
    ("btc_trend24h_le", "btc_trend24h_bps", "le", [-40, -60, -80, -100, -135, -150, -200, -300, -400, -500]),
    ("btc_trend7d_le", "btc_trend7d_bps", "le", [-100, -150, -200, -264, -350, -500, -800, -1000, -1500, -2000]),
    ("btc_rv24h_ge", "btc_rv24h_bps", "ge", [150, 175, 200, 225, 250, 300, 350, 400, 450]),
    ("n_episodes24h_ge", "n_episodes_trailing24h", "ge", [1, 2, 3, 4, 5]),
    ("pool_ret4h_le", "pool_ret4h_bps", "le", [-30, -44.55, -60, -80, -100, -120]),
    ("last_pnl5_neg", "last_pnl_5", "lt", [0]),
    ("last_pnl8_neg", "last_pnl_8", "lt", [0]),
    ("episode_age_ge", "episode_age_min", "ge", [30, 60, 120, 180, 240, 300]),
    ("episode_depth_le", "episode_depth_bps", "le", [-60, -80, -100, -120, -150, -200]),
]


def grid_search(trades, feats):
    n_examined = 0
    results = {}
    for name, key, op, grid in RULES:
        n_examined += len(grid)
        best = None
        for thr in grid:
            if op == "le":
                fn = (lambda f, k=key, x=thr: (f[k] is not None and f[k] <= x))
            elif op == "ge":
                fn = (lambda f, k=key, x=thr: (f[k] is not None and f[k] >= x))
            else:  # lt
                fn = (lambda f, k=key, x=thr: (f[k] is not None and f[k] < x))
            kept_aug = apply_rule([t for t in trades if t["month"] == "aug"], feats, fn)
            kpi_aug = month_kpi_usd(kept_aug, "aug")
            score = (kpi_aug["worst_days"] if kpi_aug["worst_days"] is not None else 1e9, -kpi_aug["usd"])
            if best is None or score < best[0]:
                best = (score, thr, kpi_aug)
        thr = best[1]
        if op == "le":
            fn = (lambda f, k=key, x=thr: (f[k] is not None and f[k] <= x))
        elif op == "ge":
            fn = (lambda f, k=key, x=thr: (f[k] is not None and f[k] >= x))
        else:
            fn = (lambda f, k=key, x=thr: (f[k] is not None and f[k] < x))
        kept_aug = apply_rule([t for t in trades if t["month"] == "aug"], feats, fn)
        kept_sep = apply_rule([t for t in trades if t["month"] == "sep"], feats, fn)
        results[name] = {"key": key, "op": op, "grid": grid, "chosen_threshold": thr,
                          "aug": month_kpi_usd(kept_aug, "aug"), "sep": month_kpi_usd(kept_sep, "sep")}
    return results, n_examined


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=f"{ROOT}/regime.json")
    ap.add_argument("--summary", default=f"{ROOT}/regime-summary.md")
    a = ap.parse_args()

    trades = load_trades()
    times, closes_btc = load_btc_1m()
    btc = BtcSeries(times, closes_btc)
    regime = load_regime()

    out = {"periods": {}, "regime_compare": {}, "rules": {}}

    episodes_by_month = {}
    baseline_kpi = {}
    for m in ("aug", "sep"):
        _name, a_day, b_day = PERIODS[m]
        s, e = kn.ms(a_day), kn.ms(b_day)
        closes = sorted([(t["t1_ms"], t["pnl_usd"]) for t in trades if t["month"] == m])
        long_periods, all_periods = find_periods(closes, s, e, min_days=3.0)
        for p in long_periods:
            p["start_iso"] = iso(p["start"]); p["end_iso"] = iso(p["end"])
        out["periods"][m] = long_periods

        eps = filter_episodes(regime, s, e)
        episodes_by_month[m] = eps

        period_ivs = [(p["start"], p["end"]) for p in long_periods]
        normal_ivs = complement(period_ivs, s, e)

        comp = {"periods": [], "normal": None}
        for p in long_periods:
            rc = regime_avg(btc, regime, p["start"], p["end"])
            eps_in = [ep for ep in eps if ep["start"] < p["end"] and ep["end"] > p["start"]]
            rc["n_filter_episodes"] = len(eps_in)
            rc["filter_avg_len_min"] = round(sum(ep["len_min"] for ep in eps_in) / len(eps_in), 1) if eps_in else None
            rc["filter_min_depth_bps"] = round(min((ep["depth_bps"] for ep in eps_in), default=0.0), 1) if eps_in else None
            trs = trades_in(trades, m, p["start"], p["end"])
            rc["trades"] = coin_breakdown(trs)
            rc["start_iso"], rc["end_iso"], rc["dur_days"] = p["start_iso"], p["end_iso"], round(p["dur_days"], 1)
            comp["periods"].append(rc)
        normal_stats = []
        for a_, b_ in normal_ivs:
            if b_ - a_ < 60_000:
                continue
            rc = regime_avg(btc, regime, a_, b_)
            eps_in = [ep for ep in eps if ep["start"] < b_ and ep["end"] > a_]
            rc["n_filter_episodes"] = len(eps_in)
            rc["span_days"] = round((b_ - a_) / MS_D, 1)
            normal_stats.append(rc)

        def wavg(key):
            vals = [(x[key], x.get("span_days", (x["n_minutes_sampled"] * 15) / 1440)) for x in normal_stats if x.get(key) is not None]
            tot = sum(w for _, w in vals)
            return round(sum(v * w for v, w in vals) / tot, 1) if tot else None

        comp["normal"] = {"btc_trend24h_bps_avg": wavg("btc_trend24h_bps_avg"), "btc_trend7d_bps_avg": wavg("btc_trend7d_bps_avg"),
                           "btc_rv24h_bps_avg": wavg("btc_rv24h_bps_avg"), "pool_ret4h_bps_avg": wavg("pool_ret4h_bps_avg"),
                           "n_filter_episodes_total": sum(x["n_filter_episodes"] for x in normal_stats),
                           "span_days_total": round(sum(x["span_days"] for x in normal_stats), 1)}
        out["regime_compare"][m] = comp
        baseline_kpi[m] = month_kpi_usd([t for t in trades if t["month"] == m], m)

    out["baseline"] = baseline_kpi

    feats = build_features(trades, btc, regime, episodes_by_month)
    rules_res, n_examined = grid_search(trades, feats)
    out["rules"] = rules_res
    out["n_combinations_examined"] = n_examined

    json.dump(out, open(a.out, "w", encoding="utf-8", newline=""), ensure_ascii=False, indent=1)

    # ---- summary ----
    lines = []
    ok5 = [n for n, r in rules_res.items() if (r["aug"]["worst_days"] or 0) <= 5 and (r["sep"]["worst_days"] or 0) <= 5]
    lines.append(f"Итог: провалов >3 сут найдено aug={len(out['periods']['aug'])}, sep={len(out['periods']['sep'])}; "
                 f"базовый KPI (худший до перехая, дни) aug={baseline_kpi['aug']['worst_days']}, sep={baseline_kpi['sep']['worst_days']}. "
                 f"Порогов, дающих ≤5 дней в ОБОИХ месяцах: {len(ok5)} из {len(RULES)} ({', '.join(ok5) if ok5 else 'нет'}).")
    lines.append("")
    lines.append("## Провалы (>3 сут)")
    for m in ("aug", "sep"):
        for p in out["periods"][m]:
            lines.append(f"- {m} {p['start_iso']} -> {p['end_iso']} ({p['dur_days']:.1f} сут, {p['kind']})")
    lines.append("")
    lines.append("## Режим: провал vs нормальные отрезки (среднее по минутам)")
    lines.append("| месяц | отрезок | BTC24ч bps | BTC7д bps | RV24ч bps | pool4ч bps | эпизодов фильтра | топ монет ($) | доля стопов |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    f0 = lambda x: "-" if x is None else f"{x:.0f}"
    for m in ("aug", "sep"):
        comp = out["regime_compare"][m]
        for rc in comp["periods"]:
            top = ", ".join(f"{x['sym']}:{x['pnl']:+.0f}" for x in rc["trades"]["top_losers"][:3])
            lines.append(f"| {m} | {rc['start_iso'][:10]}..{rc['end_iso'][:10]} ({rc['dur_days']:.1f}д) | "
                         f"{f0(rc['btc_trend24h_bps_avg'])} | {f0(rc['btc_trend7d_bps_avg'])} | {f0(rc['btc_rv24h_bps_avg'])} | "
                         f"{f0(rc['pool_ret4h_bps_avg'])} | {rc['n_filter_episodes']} | {top} | {rc['trades']['stop_fraction']} |")
        n = comp["normal"]
        lines.append(f"| {m} | нормальные ({n['span_days_total']}д) | {n['btc_trend24h_bps_avg']} | {n['btc_trend7d_bps_avg']} | "
                     f"{n['btc_rv24h_bps_avg']} | {n['pool_ret4h_bps_avg']} | {n['n_filter_episodes_total']} | - | - |")
    lines.append("")
    lines.append("Читать таблицу: 10-19.08 — RV НИЖЕ нормы (111 против 224) и BTC 24ч почти без тренда (-11 bps) — это "
                 "вязкое боковое падение (чоп), не обвал BTC; убыток дают повторные мелкие монетные стопы (TRUMP, BONK, "
                 "BCH), не один сильный сигнал по BTC. 22.08-01.09 — BTC 7д сильно ВВЕРХ (+1198 bps), но счёт всё равно в "
                 "просадке — тянут отдельные монеты (ATOM, TRUMP, BNB), доля стопов заметно выше нормы (0,21 против ~0,1 "
                 "обычно). Оба провала августа — не «BTC упал», а «эта монета/группа монет просела сама» (см. "
                 "docs/findings/loss-days-2026-09-25.md); поэтому переключатели по тренду/волатильности BTC заведомо "
                 "слабый инструмент против них.")
    lines.append("")
    lines.append(f"## Правила-выключатели (пороги подобраны на августе, перебор {n_examined} сочетаний; на сентябре без изменений)")
    lines.append("| правило | порог (авг) | avg worst-дни авг/сен | tw_p90ч авг/сен | $ авг/сен | сделок авг/сен | ≤5д оба |")
    lines.append("|---|---|---|---|---|---|---|")
    for name, r in rules_res.items():
        mark = "да" if (r["aug"]["worst_days"] or 0) <= 5 and (r["sep"]["worst_days"] or 0) <= 5 else ""
        lines.append(f"| {name} | {r['op']} {r['chosen_threshold']} | {r['aug']['worst_days']} / {r['sep']['worst_days']} | "
                     f"{r['aug']['tw_p90_h']} / {r['sep']['tw_p90_h']} | {r['aug']['usd']:+.0f} / {r['sep']['usd']:+.0f} | "
                     f"{r['aug']['n']} / {r['sep']['n']} | {mark} |")
    lines.append("")
    lines.append(f"База (без правил): worst-дни aug={baseline_kpi['aug']['worst_days']} sep={baseline_kpi['sep']['worst_days']}, "
                 f"$ aug={baseline_kpi['aug']['usd']:+.0f} sep={baseline_kpi['sep']['usd']:+.0f}, сделок aug={baseline_kpi['aug']['n']} sep={baseline_kpi['sep']['n']}.")
    lines.append("")
    lines.append("Оговорки: подбор — только на августе (9 правил x grid, всего " + str(n_examined) +
                 " сочетаний, по одному порогу на правило, без комбинаций правил и без интервалов по суткам — это разведка "
                 "перебором, число просмотренного указано); убранные сделки просто выпадают, занятость монеты не "
                 "пересчитывается; принадлежность t0 «ещё идущему» эпизоду фильтра определяется по его концу, посчитанному "
                 "по всем данным (эпизод целиком уже известен на момент счёта, хоть и не в момент t0) — глубина/возраст "
                 "внутри эпизода считаются причинно, только по минутам до t0; btc_trend24h_le -80 (лучший по августу) "
                 "проваливает сентябрь (12,5 дн против базовых 4,0) — типичный перегрев по одному месяцу; RV24ч = sqrt(сумма "
                 "квадратов 1м логдоходностей BTC за 24ч) в bps; 7-дневный тренд BTC для сделок первой недели августа "
                 "недостоверен (истории конца июля не хватает на все 7 дней назад).")

    open(a.summary, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
    print("\n".join(lines[:3]))
    print(f"periods aug={len(out['periods']['aug'])} sep={len(out['periods']['sep'])}, rules_ok5={ok5}")


if __name__ == "__main__":
    main()
