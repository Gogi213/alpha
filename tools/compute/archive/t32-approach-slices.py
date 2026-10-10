#!/usr/bin/env python3
"""T-32 «срезы по номеру подхода» (владелец 27.09, через CEO: «оч странно разложил по подходам… очень
кривое искажение биасом. детализируй по срезам») — переразбор `docs/findings/retries-2026-09-27.md`
§2/§5/§6 по плану, утверждённому Судьёй до счёта (`docs/research/reviews/retries-slices-plan-2026-09-27.md`).

**Смысл номера подхода.** `approach_number_at_entry` (1-based) — история стены ДО нашего входа: сколько
раз цена подходила к ней с рождения (кэш T28: `approach_index` 0-based + 1). Это НЕ «наша N-я попытка
входа» — повторов входа от одной и той же стены за оба месяца всего 2 (см. `t32-retries-summary.md`
§0). «Только N-й» ниже — фильтр по истории стены на момент входа, а не правило серии повторов.

Данные (готовятся отдельно): `data/t32/main-trades.csv` (сделки главного варианта, net_bps/fill/pnl_usd/
reason) + `data/t32/slices-trades.csv` (признаки стены на входе — Steam Deck,
`tmp-t32/slices_features.py`, кэш `study/approaches-t28/D20`, инструменты `study/root-<день>/
instruments.csv`, режим `study/regime`, минутные свечи `study/klines`).

Корзины — заданы ДО счёта (Судья п.8), без подгонки:
  возраст стены: 45–60 мин / 1–2 ч / 2–4 ч / 4 ч+ (гейт входа — 45 мин, поэтому корзин пять быть не может)
  размер стены $: <10k / 10–25k / 25–50k / 50–100k / 100–250k / 250k+ (корридоры П-05, `docs/research/
    P-05-wall-age-size.md` §5: номинал U ∈ {10k,25k,50k,100k,250k})
  время с прошлого подхода: нет прошлого / <15 мин / 15–60 мин / 1–4 ч / 4 ч+
  глубина BTC за 4ч и минута эпизода: терцили АВГУСТА (btc_bps_entry, тот же порог главного ≤ −44,55),
    применены к обоим месяцам без пересчёта на сентябре; минута эпизода 0–30 / 30–120 / 120+ мин
  семейство волатильности: RV за сутки (`rv_24h`, exit-sim.py) ≤ 262 bps = «спокойная» (Г-25/Г-26); Судья
    предлагал сводный ранг E32 (13 оценок) — здесь НЕ считался (бюджет времени), это упрощение, отмечено
    в оговорках.

disarm_reason ПРЕДЫДУЩЕГО (уже закрытого к моменту входа, без заглядывания вперёд) подхода → 3 класса:
  touch       → «отскок» (цена дошла до стены и подход закрылся приходом на неё — стена дожила до следующего подхода)
  price_left  → «ушла»   (цена отошла, подход разоружился без контакта)
  level_death → «пробой» (стена умерла на этом подходе — по построению у неё уже нет следующего подхода;
                          ожидание: 0 случаев как «предыдущий» — проверяется явно, не вырожденность замалчивается)

Единица — n, суток со сделками, $, bps/сделку (не только $ — фрагмент лестницы виден отдельно долей
исполнения fill), доля в плюс, доля стопов. Интервал для bps/сделку — блочный бутстреп по суткам
(кластер = сутки, 2000 повторов), только при n ≥ 30 И суток ≥ 10; 10–29 сделок — «описание» (без
интервала); < 10 — «мало» (печатается n и $, остальное не читается). «Без номера» (ambiguous/без связи)
исключены из «только N-й»/«первые K»/«с N-го» (Судья п.3) — отдельной строкой, правила их не режут.

Число клеток по всем срезам и обоим месяцам — в шапку отчёта (это карта, не поиск; любое правило отсюда
проверяется только новым протоколом, подбор на августе → проверка на сентябре).

    python tools/compute/t32-approach-slices.py --out data/t32/approach-slices.json \
        --summary data/t32/approach-slices-summary.md
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib.util
import math
import random

MAIN = "data/t32/main-trades.csv"
SLICES = "data/t32/slices-trades.csv"
MONTHS = ("aug", "sep")
QUIET_RV = 0.0262

AGE_BUCKETS = [("45-60мин", 2_700_000, 3_600_000), ("1-2ч", 3_600_000, 7_200_000),
               ("2-4ч", 7_200_000, 14_400_000), ("4ч+", 14_400_000, None)]
SIZE_BUCKETS = [("<10k", 0, 10_000), ("10-25k", 10_000, 25_000), ("25-50k", 25_000, 50_000),
                ("50-100k", 50_000, 100_000), ("100-250k", 100_000, 250_000), ("250k+", 250_000, None)]
GAP_BUCKETS = [("<15мин", 0, 900_000), ("15-60мин", 900_000, 3_600_000), ("1-4ч", 3_600_000, 14_400_000),
               ("4ч+", 14_400_000, None)]
EP_MIN_BUCKETS = [("0-30", 0, 30), ("30-120", 30, 120), ("120+", 120, None)]
N_BEFORE45_BUCKETS = [("0", 0, 1), ("1", 1, 2), ("2", 2, 3), ("3+", 3, None)]
DISARM_MAP = {"touch": "отскок", "price_left": "ушла", "level_death": "пробой"}
H_HOURS = 5 * 24


def ms(day):
    return int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def day_str(t_ms):
    return dt.datetime.fromtimestamp(t_ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def bucket_of(v, defs):
    if v is None:
        return None
    for name, lo, hi in defs:
        if v >= lo and (hi is None or v < hi):
            return name
    return None


def load_mod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def fnum(v):
    return float(v) if v not in (None, "") else None


def load():
    main = {}
    with open(MAIN, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            main[(r["month"], r["sym"], int(r["t0_ms"]))] = r
    rows = []
    with open(SLICES, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            key = (r["month"], r["sym"], int(r["t0_ms"]))
            m = main.get(key)
            t0 = int(r["t0_ms"])
            row = {"month": r["month"], "sym": r["sym"], "t0": t0, "t1": int(r["t1_ms"]),
                   "day": day_str(t0), "status": r["status"],
                   "pnl": float(m["pnl_usd"]) if m else float(r["pnl_usd"]),
                   "reason": (m["reason"] if m else r["reason"]),
                   "net_bps": fnum(m["net_bps"]) if m else None, "fill": fnum(m["fill"]) if m else None,
                   "N": int(float(r["approach_number_at_entry"])) if r.get("approach_number_at_entry") not in (None, "") else None,
                   "age_ms": fnum(r.get("age_ms_entry")), "wall_usd": fnum(r.get("wall_usd")),
                   "n_before_45min": fnum(r.get("n_before_45min")),
                   "traded_serial": fnum(r.get("traded_serial_at_entry")),
                   "gap_ms": fnum(r.get("gap_since_prev_ms")), "prev_reason": r.get("prev_disarm_reason") or None,
                   "btc_bps": fnum(r.get("btc_bps_entry")), "ep_min": fnum(r.get("episode_minute")),
                   "rv24h": fnum(r.get("rv24h")), "family": r.get("family") or None}
            row["N_bucket"] = str(row["N"]) if row["N"] is not None and row["N"] < 7 else ("7+" if row["N"] is not None else None)
            rows.append(row)
    if len(rows) != len(main):
        raise SystemExit(f"строк slices {len(rows)} != строк main-trades {len(main)} -- пересобрать slices-trades.csv")
    return rows


def boot_bps_ci(ts, reps=2000, seed=7):
    by_day = {}
    for t in ts:
        if t["net_bps"] is not None:
            by_day.setdefault(t["day"], []).append(t["net_bps"])
    days = sorted(by_day)
    if len(days) < 10:
        return None
    rng = random.Random(seed)
    out = []
    for _ in range(reps):
        vals = [x for _ in range(len(days)) for x in by_day[days[rng.randrange(len(days))]]]
        if vals:
            out.append(sum(vals) / len(vals))
    if not out:
        return None
    out.sort()
    return round(out[int(0.025 * len(out))], 1), round(out[int(0.975 * len(out))], 1)


N_CELLS_VIEWED = 0


def cellstats(ts, want_ci=False):
    global N_CELLS_VIEWED
    N_CELLS_VIEWED += 1
    n = len(ts)
    if n == 0:
        return {"n": 0, "note": "нет сделок"}
    days = sorted(set(t["day"] for t in ts))
    usd = round(sum(t["pnl"] for t in ts), 2)
    bps_vals = [t["net_bps"] for t in ts if t["net_bps"] is not None]
    bps = round(sum(bps_vals) / len(bps_vals), 1) if bps_vals else None
    win = round(sum(1 for t in ts if t["pnl"] > 0) / n, 3)
    stop = round(sum(1 for t in ts if t["reason"] == "stop") / n, 3)
    fills = [t["fill"] for t in ts if t["fill"] is not None]
    fill = round(sum(fills) / len(fills), 3) if fills else None
    out = {"n": n, "n_days": len(days), "usd": usd, "bps": bps, "win": win, "stop": stop, "fill": fill}
    if n < 10:
        out["note"] = "мало"
    elif n < 30 or len(days) < 10:
        out["note"] = "описание"
    else:
        out["note"] = ""
        if want_ci:
            ci = boot_bps_ci(ts)
            if ci:
                out["bps_ci"] = list(ci)
    return out


def cell_line(v):
    if v["n"] == 0:
        return "нет сделок"
    tag = f" ({v['note']})" if v.get("note") else ""
    ci = f" [{v['bps_ci'][0]};{v['bps_ci'][1]}]" if v.get("bps_ci") else ""
    fill = f" fill{v['fill']*100:.0f}%" if v.get("fill") is not None else ""
    return f"n={v['n']}({v['n_days']}д) ${v['usd']:+.0f} {v['bps']:+.0f}bps{ci}{fill} win{v['win']*100:.0f}% stop{v['stop']*100:.0f}%{tag}"


def cross_table(linked, dim_key, dim_buckets_order, title):
    """N (1..6,7+) x dim -> cellstats, по месяцам; dim_buckets_order уже посчитаны и лежат в t[dim_key]."""
    out = {}
    for m in MONTHS:
        out[m] = {}
        for b in dim_buckets_order:
            row = {}
            for nb in ("1", "2", "3", "4", "5", "6", "7+"):
                ts = [t for t in linked if t["month"] == m and t["N_bucket"] == nb and t[dim_key] == b]
                row[nb] = cellstats(ts)
            out[m][b] = row
    return {"title": title, "table": out}


def pooled_1_vs_2plus(linked, dim_key, dim_buckets_order):
    """Судья п.2: 1-й против 2+ внутри корзины возраста, усреднено по корзинам весом n (упрощённый суррогат
    Мантель-Хензеля -- взвешенное среднее разности долей/bps, не полные веса М-Х; помечено в оговорках)."""
    out = {}
    for m in MONTHS:
        rows = []
        for b in dim_buckets_order:
            g1 = [t for t in linked if t["month"] == m and t[dim_key] == b and t["N"] == 1]
            g2 = [t for t in linked if t["month"] == m and t[dim_key] == b and t["N"] is not None and t["N"] >= 2]
            if len(g1) < 5 or len(g2) < 5:
                continue
            w = len(g1) + len(g2)
            win_d = (sum(1 for t in g2 if t["pnl"] > 0) / len(g2)) - (sum(1 for t in g1 if t["pnl"] > 0) / len(g1))
            b1 = [t["net_bps"] for t in g1 if t["net_bps"] is not None]
            b2 = [t["net_bps"] for t in g2 if t["net_bps"] is not None]
            bps_d = (sum(b2) / len(b2) - sum(b1) / len(b1)) if b1 and b2 else None
            rows.append({"bucket": b, "n1": len(g1), "n2p": len(g2), "win_diff": round(win_d, 3),
                         "bps_diff": round(bps_d, 1) if bps_d is not None else None, "w": w})
        if rows:
            tw = sum(r["w"] for r in rows)
            pooled_win = round(sum(r["win_diff"] * r["w"] for r in rows) / tw, 3)
            bps_rows = [r for r in rows if r["bps_diff"] is not None]
            pooled_bps = round(sum(r["bps_diff"] * r["w"] for r in bps_rows) / sum(r["w"] for r in bps_rows), 1) if bps_rows else None
        else:
            pooled_win = pooled_bps = None
        out[m] = {"by_bucket": rows, "pooled_win_diff_2plus_vs_1": pooled_win, "pooled_bps_diff_2plus_vs_1": pooled_bps}
    return out


def kpi_decomp(kn, closes):
    """Судья (kpi-days-to-new-high-2026-09-27.md §Дополнение п.3): доля часов «ниже пика» (просадка) против
    «на пике без новых сделок» (простой из-за отсутствия сигналов, не из-за убытка) -- KPI подмножеств с малым
    числом сделок иначе несравним с базой."""
    import bisect
    ev = sorted(closes)
    times = [t for t, _ in ev]
    eq, c = [], 0.0
    for _, p in ev:
        c += p
        eq.append(c)
    pm, mx = [], 0.0
    for v in eq:
        mx = max(mx, v)
        pm.append(mx)
    s0, e0 = ms("2026-08-01"), ms("2026-09-24")
    cutoff = e0 - H_HOURS * 3_600_000
    out = {m: {"below_peak": 0, "at_peak": 0} for m in MONTHS}
    t = s0
    while t <= cutoff:
        pk = "aug" if t < ms("2026-09-01") else "sep"
        i = bisect.bisect_right(times, t)
        cur = eq[i - 1] if i > 0 else 0.0
        top = pm[i - 1] if i > 0 else 0.0
        if cur >= top - 1e-9:
            out[pk]["at_peak"] += 1
        else:
            out[pk]["below_peak"] += 1
        t += 3_600_000
    for m in MONTHS:
        tot = out[m]["at_peak"] + out[m]["below_peak"]
        out[m]["at_peak_share"] = round(out[m]["at_peak"] / tot, 3) if tot else None
        out[m]["below_peak_share"] = round(out[m]["below_peak"] / tot, 3) if tot else None
        out[m]["n_main"] = tot
    return out


def rules_kpi(kn, linked_only):
    """Судья п.3/п.6: «без номера» исключены совсем (не «не режутся» как в прежней версии); главные правила --
    «только N-й» (1..6,7+); «первые K»/«с N-го» -- производные (накопительные), вторичные."""

    def closes_of(ts, m=None):
        return [(t["t1"], t["pnl"]) for t in ts if m is None or t["month"] == m]

    def kpi(kept):
        rout = {}
        for m in MONTHS:
            mm = kn.month_metrics(closes_of(kept, m), m)
            rout[m] = {"n": mm["n"], "usd": round(mm["usd"], 2)}
        roll = kn.rolling_kpi(closes_of(kept), h_days=5)
        decomp = kpi_decomp(kn, closes_of(kept))
        for m in MONTHS:
            r = roll[m]
            rout[m]["frac_gt_5d"] = r["frac_gt_h"]
            rout[m]["n_main"] = r["n_main"]
            rout[m]["p90_days"] = None if r["max"]["p90"] is None else round(r["max"]["p90"] / 24, 1)
            rout[m]["p90_censored"] = r["max"]["p90_censored"]
            rout[m]["below_peak_share"] = decomp[m]["below_peak_share"]
            rout[m]["at_peak_share"] = decomp[m]["at_peak_share"]
        return rout

    rules = []
    rules.append({"rule": "все (база, только связанные)", **kpi(linked_only)})
    for N in range(1, 7):
        rules.append({"rule": f"только {N}-й", **kpi([t for t in linked_only if t["N"] == N])})
    rules.append({"rule": "только 7+", **kpi([t for t in linked_only if t["N"] is not None and t["N"] >= 7])})
    for K in range(1, 7):
        rules.append({"rule": f"первые {K} (производное)", **kpi([t for t in linked_only if t["N"] is not None and t["N"] <= K])})
    for N in range(2, 7):
        rules.append({"rule": f"с {N}-го и дальше (производное)", **kpi([t for t in linked_only if t["N"] is not None and t["N"] >= N])})
    return rules


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data/t32/approach-slices.json")
    ap.add_argument("--summary", default="data/t32/approach-slices-summary.md")
    a = ap.parse_args()

    kn = load_mod("tools/compute/kpi-newhigh.py", "kn")
    rows = load()
    linked = [t for t in rows if t["status"] == "linked"]
    unknown = [t for t in rows if t["status"] != "linked"]

    # терцили августа: btc_bps_entry (глубже = более отрицательно) и минута эпизода -- фиксируются здесь
    aug_btc = sorted(t["btc_bps"] for t in linked if t["month"] == "aug" and t["btc_bps"] is not None)
    c1, c2 = aug_btc[len(aug_btc) // 3], aug_btc[2 * len(aug_btc) // 3]

    def btc_bucket(v):
        if v is None:
            return None
        return "глубокий" if v <= c1 else ("средний" if v <= c2 else "неглубокий")

    for t in linked:
        t["age_bucket"] = bucket_of(t["age_ms"], AGE_BUCKETS)
        t["size_bucket"] = bucket_of(t["wall_usd"], SIZE_BUCKETS)
        t["gap_bucket"] = "нет прошлого" if t["gap_ms"] is None and t["N"] == 1 else bucket_of(t["gap_ms"], GAP_BUCKETS)
        t["outcome_bucket"] = ("нет прошлого" if t["prev_reason"] and "нет" in t["prev_reason"]
                                else DISARM_MAP.get(t["prev_reason"], t["prev_reason"]))
        t["nb45_bucket"] = bucket_of(t["n_before_45min"], N_BEFORE45_BUCKETS)
        t["traded_serial_bucket"] = (str(int(t["traded_serial"])) if t["traded_serial"] is not None and t["traded_serial"] < 7
                                      else ("7+" if t["traded_serial"] is not None else None))
        t["btc_bucket"] = btc_bucket(t["btc_bps"])
        t["ep_bucket"] = bucket_of(t["ep_min"], EP_MIN_BUCKETS)

    # 1) N x возраст (главная таблица Судьи п.2) + сырая по N (смешано с возрастом)
    t1 = cross_table(linked, "age_bucket", [b[0] for b in AGE_BUCKETS], "N x возраст стены")
    pooled_age = pooled_1_vs_2plus(linked, "age_bucket", [b[0] for b in AGE_BUCKETS])
    raw_by_N = {m: {nb: cellstats([t for t in linked if t["month"] == m and t["N_bucket"] == nb], want_ci=True)
                     for nb in ("1", "2", "3", "4", "5", "6", "7+")} for m in MONTHS}

    # 2) N x размер стены
    t2 = cross_table(linked, "size_bucket", [b[0] for b in SIZE_BUCKETS], "N x размер стены $")

    # 3) N x время с прошлого / N x исход прошлого
    gap_order = ["нет прошлого"] + [b[0] for b in GAP_BUCKETS]
    t3a = cross_table(linked, "gap_bucket", gap_order, "N x время с прошлого подхода")
    outcome_order = ["нет прошлого", "отскок", "ушла", "пробой"]
    t3b = cross_table(linked, "outcome_bucket", outcome_order, "N x исход прошлого подхода")
    outcome_counts = {o: sum(1 for t in linked if t["outcome_bucket"] == o) for o in outcome_order}

    # 4) N x число подходов до 45 мин; отдельно -- разбивка по traded_serial (номер среди торгуемых)
    t4a = cross_table(linked, "nb45_bucket", [b[0] for b in N_BEFORE45_BUCKETS], "N x подходов до 45 мин")
    traded_serial_tab = {m: {nb: cellstats([t for t in linked if t["month"] == m and t["traded_serial_bucket"] == nb], want_ci=True)
                              for nb in ("1", "2", "3", "4", "5", "6", "7+")} for m in MONTHS}

    # 5) N x глубина BTC / N x минута эпизода
    t5a = cross_table(linked, "btc_bucket", ["глубокий", "средний", "неглубокий"], "N x глубина просадки BTC (терцили августа)")
    t5b = cross_table(linked, "ep_bucket", [b[0] for b in EP_MIN_BUCKETS], "N x минута эпизода")

    # 6) N x семейство волатильности
    t6 = cross_table(linked, "family", ["quiet", "volatile"], "N x семейство волатильности (RV24ч <=262bps)")

    rules = rules_kpi(kn, linked)
    unknown_stats = {m: cellstats([t for t in unknown if t["month"] == m]) for m in MONTHS}

    n_cells = N_CELLS_VIEWED
    out = {"n_trades": {"linked": len(linked), "unknown": len(unknown)}, "n_cells_viewed": n_cells,
           "btc_tertile_cuts_aug": [round(c1, 2), round(c2, 2)],
           "1_age": t1, "1_pooled_1_vs_2plus_by_age": pooled_age, "1_raw_by_N": raw_by_N,
           "2_size": t2, "3_gap": t3a, "3_outcome": t3b, "outcome_counts": outcome_counts,
           "4_before45": t4a, "4_traded_serial": traded_serial_tab, "5_btc": t5a, "5_episode_minute": t5b,
           "6_family": t6, "rules": rules, "unknown_by_month": unknown_stats}
    import json
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    write_summary(a.summary, out)
    print(open(a.summary, encoding="utf-8").read()[:4000])


def write_summary(path, out):
    L = ["# Срезы по номеру подхода стены (§7, детализация `retries-2026-09-27.md` §2/§5/§6)", "",
         "**Номер подхода — история стены до входа (сколько раз к ней подходили с рождения), не номер нашей "
         "попытки входа.** План — `docs/research/reviews/retries-slices-plan-2026-09-27.md` (Судья, 9 правок до счёта).",
         "", f"Сделок: связанных {out['n_trades']['linked']}, без номера (ambiguous) {out['n_trades']['unknown']} "
             f"(в срезах ниже не участвуют — правило их не режет, отдельная строка). Клеток просмотрено: "
             f"{out['n_cells_viewed']} (карта, не поиск; правило из неё — только новым протоколом на августе/сентябре).",
         f"\nТерцили августа (btc_bps_entry, глубже = более отрицательно): порог глубокий/средний "
         f"{out['btc_tertile_cuts_aug'][0]} bps, средний/неглубокий {out['btc_tertile_cuts_aug'][1]} bps (применены "
         "к обоим месяцам без пересчёта на сентябре).", ""]

    def N_row(tab_month, order=("1", "2", "3", "4", "5", "6", "7+")):
        return " | ".join(f"{nb}: {cell_line(tab_month[nb])}" for nb in order if tab_month[nb]["n"] > 0)

    L.append("## 1. N x возраст стены на входе (главная таблица; корзины 45–60мин/1–2ч/2–4ч/4ч+ заданы до счёта)\n")
    for m in MONTHS:
        L.append(f"### {m}")
        for b in ("45-60мин", "1-2ч", "2-4ч", "4ч+"):
            L.append(f"- **{b}**: " + N_row(out["1_age"]["table"][m][b]))
        L.append("")
    L.append("### 1-й против 2+ внутри корзины возраста, взвешено по n (упрощённый суррогат Мантель-Хензеля)\n")
    for m in MONTHS:
        p = out["1_pooled_1_vs_2plus_by_age"][m]
        L.append(f"- {m}: пул Δ доли в плюс (2+ − 1) = {p['pooled_win_diff_2plus_vs_1']}, пул Δ bps/сделку (2+ − 1) = "
                  f"{p['pooled_bps_diff_2plus_vs_1']}; по корзинам: " +
                  "; ".join(f"{r['bucket']} n1={r['n1']} n2+={r['n2p']} Δwin={r['win_diff']:+.2f} Δbps={r['bps_diff']}"
                            for r in p["by_bucket"]))
    L.append("\n### Сырая строка по N (смешано с возрастом — не читать как эффект номера)\n")
    for m in MONTHS:
        L.append(f"- {m}: " + N_row(out["1_raw_by_N"][m]))

    L.append("\n## 2. N x размер стены $ (корридоры П-05)\n")
    for m in MONTHS:
        L.append(f"### {m}")
        for b in ("<10k", "10-25k", "25-50k", "50-100k", "100-250k", "250k+"):
            L.append(f"- **{b}**: " + N_row(out["2_size"]["table"][m][b]))
        L.append("")

    L.append("## 3. N x время с прошлого подхода / N x исход прошлого подхода\n")
    L.append("Соответствие disarm_reason -> исход: touch -> «отскок», price_left -> «ушла», level_death -> «пробой» "
              f"(проверка вырожденности — счётчик предыдущих исходов: {out['outcome_counts']}; «пробой» как ИСХОД "
              "ПРЕДЫДУЩЕГО подхода — 0 случаев по построению: стена, у которой предыдущий подход кончился гибелью "
              "уровня, не доживает до следующего подхода).\n")
    for m in MONTHS:
        L.append(f"### {m} — время с прошлого")
        for b in ["нет прошлого"] + ["<15мин", "15-60мин", "1-4ч", "4ч+"]:
            L.append(f"- **{b}**: " + N_row(out["3_gap"]["table"][m][b]))
        L.append(f"### {m} — исход прошлого")
        for b in ["нет прошлого", "отскок", "ушла", "пробой"]:
            L.append(f"- **{b}**: " + N_row(out["3_outcome"]["table"][m][b]))
        L.append("")

    L.append("## 4. N x число подходов до возраста 45 мин; отдельно — номер среди торгуемых\n")
    for m in MONTHS:
        L.append(f"### {m} — подходов до 45 мин")
        for b in ("0", "1", "2", "3+"):
            L.append(f"- **{b}**: " + N_row(out["4_before45"]["table"][m][b]))
        L.append(f"### {m} — номер среди торгуемых (traded_serial, вместо номера от рождения)")
        L.append("- " + N_row(out["4_traded_serial"][m]))
        L.append("")

    L.append("## 5. N x глубина просадки BTC / N x минута эпизода\n")
    for m in MONTHS:
        L.append(f"### {m} — глубина BTC (терцили августа)")
        for b in ("глубокий", "средний", "неглубокий"):
            L.append(f"- **{b}**: " + N_row(out["5_btc"]["table"][m][b]))
        L.append(f"### {m} — минута эпизода")
        for b in ("0-30", "30-120", "120+"):
            L.append(f"- **{b}**: " + N_row(out["5_episode_minute"]["table"][m][b]))
        L.append("")

    L.append("## 6. N x семейство волатильности (RV24ч <= 262 bps = «спокойная»; НЕ сводный ранг E32 — "
              "Судья предлагал E32, здесь упрощено до порога Г-25/Г-26 из бюджета времени, см. оговорки)\n")
    for m in MONTHS:
        L.append(f"### {m}")
        for b in ("quiet", "volatile"):
            L.append(f"- **{b}**: " + N_row(out["6_family"]["table"][m][b]))
        L.append("")

    L.append("## 7. Правила «только N-й» (главные) и «первые K»/«с N-го» (производные, накопительные) — "
              "без пересчёта занятости здесь (busy-replay — отдельный раздел ниже)\n")
    L.append("KPI: доля часов месяца с ожиданием перехая > 5 сут (без цензуры), из них доля часов «на пике без новых "
              "сделок» (простой, не убыток) против «ниже пика» (просадка) — правила с малым n иначе несравнимы с базой.\n")
    L.append("| правило | авг n | авг $ | авг >5сут | авг на_пике/ниже_пика | сен n | сен $ | сен >5сут | сен на_пике/ниже_пика |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in out["rules"]:
        def cell(m):
            x = r[m]
            gt = f"{x['frac_gt_5d']*100:.0f}%" if x['frac_gt_5d'] is not None else "—"
            pk = f"{x['at_peak_share']*100:.0f}%/{x['below_peak_share']*100:.0f}%" if x['at_peak_share'] is not None else "—"
            return f"{x['n']} | {x['usd']:+.0f} | {gt} | {pk}"
        L.append(f"| {r['rule']} | " + cell("aug") + " | " + cell("sep") + " |")
    L.append("")
    L.append(f"Без номера (ambiguous, исключены из правил): август {cell_line(out['unknown_by_month']['aug'])}; "
              f"сентябрь {cell_line(out['unknown_by_month']['sep'])}.")
    L.append("\nОговорки: семейство волатильности — RV24ч, не сводный ранг E32 (Судья предлагал E32); Мантель-Хензель "
              "заменён взвешенным средним (n-вес, не обратная дисперсия); busy-replay (занятость монеты) — отдельно, "
              "см. §7-busy; данные — те же дни, что и главный вариант (найдено на подборе); правило из любой клетки "
              "не идёт в вердикт без нового протокола.")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
