#!/usr/bin/env python3
"""T-32 задача `exits` (27.09) — четыре гипотезы пула правил выхода/паузы на готовых сделках главного
варианта (данные — `data/t32/BRIEF.md`). Решение в момент — только по данным до него (без заглядывания).

Цена выхода исходной сделки (реконструкция): exit = entry * (1 + (net_bps + fee_bps)/1e4), dir=1 (лонг).
Для досрочных выходов (3, 4): net_new = (close/entry - 1)*1e4 - fee_bps (fee_bps сделки, без изменений),
t1_new = конец минуты этого close; pnl_new = net_new/1e4 * usd.

1. Г-103 пауза: после закрытия сделки монеты новые входы этой монеты пропускаются C минут (сделка просто
   выпадает из счёта); C = 30/60/120/240 (для всех закрытий) и C = 120/240 только после стопа
   (reason=stop). «Последнее закрытие» — t1 последней ВЗЯТОЙ сделки этой монеты (сделки по t0).
2. Г-102 растущий отступ после стопа: после сделки монеты, закрытой стопом, следующий вход той же монеты
   разрешён только если entry ≤ exit_стопа × (1 − O(t)/1e4), O(t) = O0 × 0.5^(Δt/2ч), Δt от t1 стопа до
   нового t0; O0 = 50/100/200 bps. Не прошедшие условие — выпадают (и остаются «последней закрытой»
   сделкой монеты — следующая проверяется от того же стопа).
3. Г-108 выход при возврате под уровень: уровень = entry × (1 − x/1e4), x = 20/50/100 bps. С первой полной
   минуты после t0 (минута начала входа + 1 мин) до t1: первый close < уровень → досрочный выход по этому
   close, если он раньше исходного t1; иначе сделка как была.
4. Г-110 импульс не пошёл: через N минут после входа (минута входа + N мин), N = 15/30/60; если исходный
   выход к этому моменту ещё не случился и close ≤ entry × (1 + m/1e4), m = 0/20 bps → выход по этому close.

KPI — только tools/compute/kpi-newhigh.py:month_metrics(closes, pk), closes=[(t1_ms, pnl_usd), …].

    python tools/compute/t32-exits.py --out data/t32/exits.json --summary data/t32/exits-summary.md
"""
import argparse
import bisect
import csv
import glob
import importlib.util
import json
import os

MS_MIN = 60_000
MS_H = 3_600_000
KLINES_DIR = {"aug": "data/t32/epochs/e-aug/study/klines", "sep": "data/t32/study/klines"}

_lib_spec = importlib.util.spec_from_file_location(
    "_lib", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lib", "__init__.py"))
_lib = importlib.util.module_from_spec(_lib_spec)
_lib_spec.loader.exec_module(_lib)


def load_kn():
    spec = importlib.util.spec_from_file_location("kn", "tools/compute/kpi-newhigh.py")
    kn = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kn)
    return kn


def load_trades(path):
    # T-21 batch 2: `_lib.read_csv` вместо своей копии `open` + `csv.DictReader` (main-trades.csv без `#` в шапке).
    rows = []
    for r in _lib.read_csv(path)[1]:
        rows.append({
            "month": r["month"], "sym": r["sym"], "t0": int(r["t0_ms"]), "t1": int(r["t1_ms"]),
            "net_bps": float(r["net_bps"]), "usd": float(r["usd"]), "entry": float(r["entry"]),
            "fee_bps": float(r["fee_bps"]), "reason": r["reason"],
        })
    rows.sort(key=lambda t: t["t0"])
    return rows


def exit_price(t):
    return t["entry"] * (1 + (t["net_bps"] + t["fee_bps"]) / 1e4)


def pnl_of(t):
    return t["net_bps"] / 1e4 * t["usd"]


# ---------- klines cache ----------

_KLINES_CACHE = {}


def get_klines(month, sym):
    key = (month, sym)
    if key in _KLINES_CACHE:
        return _KLINES_CACHE[key]
    fp = os.path.join(KLINES_DIR[month], f"ref-{sym}-1m.csv")
    keys, closes = [], {}
    if os.path.exists(fp):
        with open(fp, encoding="utf-8") as f:
            rd = csv.reader(f)
            head = next(rd)
            mi, ci = head.index("minute_ms"), head.index("close")
            for row in rd:
                closes[int(row[mi])] = float(row[ci])
        keys = sorted(closes)
    _KLINES_CACHE[key] = (keys, closes)
    return keys, closes


# ---------- Г-103 пауза ----------

def variant_pause(trades, c_min, only_stop):
    last_t1, last_reason = {}, {}
    keep, dropped = [], 0
    for t in trades:
        sym = t["sym"]
        lt = last_t1.get(sym)
        skip = False
        if lt is not None:
            gate = (not only_stop) or last_reason.get(sym) == "stop"
            if gate and t["t0"] < lt + c_min * MS_MIN:
                skip = True
        if skip:
            dropped += 1
            continue
        keep.append(t)
        last_t1[sym] = t["t1"]
        last_reason[sym] = t["reason"]
    return keep, dropped


# ---------- Г-102 растущий отступ ----------

# Возврат Судьи 27.09 п.2: при O(t) -> 0 уровень стремится к цене стопа, и вход выше нее запрещён
# бессрочно (в данных — медиана отброшенных 184 ч после стопа). Г-102 в пуле — «мягче блока монеты,
# отступ плавно снижается», то есть должен СНИМАТЬСЯ, когда отступ станет пренебрежимо мал, а не
# держаться вечно. Порог снятия — OFFSET_EPS_BPS: когда O(t) < eps, запрет снимается (последний стоп
# монеты перестаёт что-либо блокировать; следующий стоп начнёт отсчёт заново). eps = 1 bps — за пределами
# минимального разрешения цены/издержек сделки, число не подбиралось под данные.
OFFSET_EPS_BPS = 1.0


def variant_offset(trades, o0, eps_bps=OFFSET_EPS_BPS):
    last_stop = {}  # sym -> (t1, exit_price)
    keep, dropped = [], 0
    for t in trades:
        sym = t["sym"]
        prev = last_stop.get(sym)
        allowed = True
        if prev is not None:
            dt_h = (t["t0"] - prev[0]) / MS_H
            o = o0 * (0.5 ** (dt_h / 2.0))
            if o >= eps_bps:
                level = prev[1] * (1 - o / 1e4)
                if t["entry"] > level:
                    allowed = False
            # o < eps_bps: отступ спал — запрет снят, last_stop монеты будет заменён/снят ниже
        if not allowed:
            dropped += 1
            continue
        keep.append(t)
        if t["reason"] == "stop":
            last_stop[sym] = (t["t1"], exit_price(t))
        else:
            last_stop.pop(sym, None)
    return keep, dropped


# ---------- Г-108 выход под уровень ----------

def early_exit_level(t, x_bps):
    level = t["entry"] * (1 - x_bps / 1e4)
    keys, closes = get_klines(t["month"], t["sym"])
    if not keys:
        return None
    minute0 = (t["t0"] // MS_MIN) * MS_MIN
    start = minute0 + MS_MIN
    i = bisect.bisect_left(keys, start)
    while i < len(keys):
        m = keys[i]
        exit_t = m + MS_MIN
        if exit_t >= t["t1"]:
            break
        if closes[m] < level:
            return exit_t, closes[m]
        i += 1
    return None


def variant_level(trades, x_bps):
    out, triggered, no_data = [], 0, 0
    for t in trades:
        keys, _ = get_klines(t["month"], t["sym"])
        if not keys:
            no_data += 1
            out.append(t)
            continue
        r = early_exit_level(t, x_bps)
        if r is None:
            out.append(t)
            continue
        exit_t, close = r
        triggered += 1
        nt = dict(t)
        nt["t1"] = exit_t
        nt["net_bps"] = (close / t["entry"] - 1) * 1e4 - t["fee_bps"]
        out.append(nt)
    return out, triggered, no_data


# ---------- Г-110 импульс не пошёл ----------

def momentum_exit(t, n_min, m_bps):
    minute0 = (t["t0"] // MS_MIN) * MS_MIN
    target = minute0 + n_min * MS_MIN
    exit_t = target + MS_MIN
    if exit_t >= t["t1"]:
        return None  # исходный выход уже случился к этому моменту
    keys, closes = get_klines(t["month"], t["sym"])
    if target not in closes:
        return None
    c = closes[target]
    if c <= t["entry"] * (1 + m_bps / 1e4):
        return exit_t, c
    return None


def variant_momentum(trades, n_min, m_bps):
    out, triggered, no_data = [], 0, 0
    for t in trades:
        keys, _ = get_klines(t["month"], t["sym"])
        if not keys:
            no_data += 1
            out.append(t)
            continue
        r = momentum_exit(t, n_min, m_bps)
        if r is None:
            out.append(t)
            continue
        exit_t, close = r
        triggered += 1
        nt = dict(t)
        nt["t1"] = exit_t
        nt["net_bps"] = (close / t["entry"] - 1) * 1e4 - t["fee_bps"]
        out.append(nt)
    return out, triggered, no_data


# ---------- KPI ----------

def closes_of(trades, month=None):
    xs = trades if month is None else [t for t in trades if t["month"] == month]
    return [(t["t1"], pnl_of(t)) for t in xs]


def metrics_for(kn, trades):
    return {"aug": kn.month_metrics(closes_of(trades, "aug"), "aug"),
            "sep": kn.month_metrics(closes_of(trades, "sep"), "sep"),
            "augsep": kn.month_metrics(closes_of(trades, None), "augsep")}


def worst_days(r):
    wa = r["aug"]["hours"]["worst"] / 24 if r["aug"]["hours"]["worst"] is not None else None
    ws = r["sep"]["hours"]["worst"] / 24 if r["sep"]["hours"]["worst"] is not None else None
    wg = r["augsep"]["hours"]["worst"] / 24 if r["augsep"]["hours"]["worst"] is not None else None
    return wa, ws, wg


def tw_p90(r):
    return r["aug"]["hours"]["tw_p90"], r["sep"]["hours"]["tw_p90"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trades", default="data/t32/main-trades.csv")
    ap.add_argument("--out", default="data/t32/exits.json")
    ap.add_argument("--summary", default="data/t32/exits-summary.md")
    a = ap.parse_args()

    kn = load_kn()
    trades = load_trades(a.trades)

    variants = {}  # name -> (trades_out, meta)
    variants["baseline"] = (trades, {})

    for c in (30, 60, 120, 240):
        ts, dropped = variant_pause(trades, c, only_stop=False)
        variants[f"g103_pause{c}"] = (ts, {"dropped": dropped})
    for c in (120, 240):
        ts, dropped = variant_pause(trades, c, only_stop=True)
        variants[f"g103_pauseStop{c}"] = (ts, {"dropped": dropped})

    for o0 in (50, 100, 200):
        ts, dropped = variant_offset(trades, o0)
        variants[f"g102_offset{o0}"] = (ts, {"dropped": dropped})

    no_data_level = 0
    for x in (20, 50, 100):
        ts, trig, nd = variant_level(trades, x)
        no_data_level = max(no_data_level, nd)
        variants[f"g108_level{x}"] = (ts, {"triggered": trig, "no_data": nd})

    no_data_mom = 0
    for n in (15, 30, 60):
        for m in (0, 20):
            ts, trig, nd = variant_momentum(trades, n, m)
            no_data_mom = max(no_data_mom, nd)
            variants[f"g110_N{n}_m{m}"] = (ts, {"triggered": trig, "no_data": nd})

    n_hyp_variants = 6 + 3 + 3 + 6  # без baseline

    results = {}
    for name, (ts, meta) in variants.items():
        r = metrics_for(kn, ts)
        rl = kn.rolling_kpi(closes_of(ts, None))  # основное определение с 27.09 (CEO): скользящий старт, непрерывный счёт
        results[name] = {"n_trades": len(ts), **meta, **r, "roll": rl}

    base = results["baseline"]
    wa0, ws0, wg0 = worst_days(base)

    out = {
        "n_variants_viewed": n_hyp_variants,
        "baseline_check": {"aug_days": wa0, "sep_days": ws0, "augsep_days": wg0,
                            "aug_usd": base["aug"]["usd"], "sep_usd": base["sep"]["usd"],
                            "augsep_usd": base["augsep"]["usd"]},
        "klines_no_data_symbols_g108": no_data_level, "klines_no_data_symbols_g110": no_data_mom,
        "results": results,
    }
    json.dump(out, open(a.out, "w", encoding="utf-8", newline=""), ensure_ascii=False, indent=1)

    h = lambda x: "—" if x is None else f"{x:.1f}"
    d = lambda x: "—" if x is None else f"{x:+.0f}"

    groups = {
        "Г-103 пауза (все закрытия)": [f"g103_pause{c}" for c in (30, 60, 120, 240)],
        "Г-103 пауза (только после стопа)": [f"g103_pauseStop{c}" for c in (120, 240)],
        "Г-102 растущий отступ после стопа": [f"g102_offset{o}" for o in (50, 100, 200)],
        "Г-108 выход под уровень": [f"g108_level{x}" for x in (20, 50, 100)],
        "Г-110 импульс не пошёл": [f"g110_N{n}_m{m}" for n in (15, 30, 60) for m in (0, 20)],
    }

    lines = []
    lines.append("# T-32 exits — четыре гипотезы выхода/паузы на сделках главного варианта\n")
    wa0s, ws0s, wg0s = h(wa0), h(ws0), h(wg0)
    lines.append(f"База (main-trades.csv без изменений): до перехая авг {wa0s} дн / сен {ws0s} дн / augsep {wg0s} дн; "
                  f"$ авг {base['aug']['usd']:+.2f} / сен {base['sep']['usd']:+.2f} / augsep {base['augsep']['usd']:+.2f} "
                  f"(сверка с BRIEF: 9,6 / 4,0 / 16,9 дн, +106,04 / +110,99).\n")
    lines.append("| вариант | сделок | скольз. старт p90/макс, дн (авг;сен) | от начала месяца, дни (авг;сен;augsep) | $ авг | $ сен | $ augsep | ≤5 дн оба мес (p90 скольз.) |")
    lines.append("|---|---|---|---|---|---|---|---|")
    best_by_group = {}
    for gname, names in groups.items():
        lines.append(f"| **{gname}** | | | | | | | |")
        best, best_key = None, None
        for name in names:
            r = results[name]
            wa, ws, wg = worst_days(r)
            R_ = r["roll"]
            pa, ps = R_["aug"]["max"]["p90"] / 24, R_["sep"]["max"]["p90"] / 24
            good = pa <= 5 and ps <= 5
            lines.append(f"| {name} | {r['n_trades']} | {pa:.1f}/{R_['aug']['max']['max'] / 24:.1f};{ps:.1f}/{R_['sep']['max']['max'] / 24:.1f} | {h(wa)};{h(ws)};{h(wg)} | {d(r['aug']['usd'])} | "
                          f"{d(r['sep']['usd'])} | {d(r['augsep']['usd'])} | {'да' if good else ''} |")
            worst_month = min(r["aug"]["usd"], r["sep"]["usd"])
            if best is None or worst_month > best:
                best, best_key = worst_month, name
        best_by_group[gname] = best_key
    lines.append("")

    ok5_all = [n for n, r in results.items() if n != "baseline"
               and r["roll"]["aug"]["max"]["p90"] <= 120 and r["roll"]["sep"]["max"]["p90"] <= 120]
    lines.append(f"≤ 5 дней до перехая в обоих месяцах: {', '.join(ok5_all) if ok5_all else 'нет вариантов'}.\n")
    best_bits = [f"{gname} — **{key}** (авг {d(results[key]['aug']['usd'])}, сен {d(results[key]['sep']['usd'])}, "
                 f"дни {h(worst_days(results[key])[0])};{h(worst_days(results[key])[1])})"
                 for gname, key in best_by_group.items()]
    lines.append("Лучший по худшему месяцу ($) внутри каждой гипотезы: " + "; ".join(best_bits) + ".\n")
    lines.append(f"Просмотрено вариантов: {n_hyp_variants} (+ baseline). Найдено на данных подбора (август/сентябрь, "
                  "те же сутки, что и главный вариант) — не проверено вне выборки.\n")
    lines.append("Оговорки: пропущенные сделки (Г-103/Г-102) просто выпадают из счёта, занятость монеты и очередь "
                  "входа не пересчитаны. Г-108/Г-110 считают по 1m close без внутриминутного пути; все 53 символа "
                  "покрыты в обоих каталогах klines (no_data = 0); занятость монеты тоже не пересчитана — ранний "
                  "выход освобождает монету раньше, а сделки, которые движок пропустил как «монета занята», в счёт "
                  "не попадают (возврат Судьи 27.09, п.6). Г-102 (27.09, возврат Судьи п.2): запрет — растущий "
                  "отступ, СНИМАЕТСЯ, когда O(t) < 1 bps (было — бессрочный запрет входа выше цены стопа; отброшено "
                  "15/21/32 сделки при O0=50/100/200 против прежних 164/164/165). Никакой t-статистики по сделкам; "
                  "интервалы по суткам не считались (объём задачи — просмотр гипотез, не вердикт).")
    md = "\n".join(lines)
    open(a.summary, "w", encoding="utf-8", newline="").write(md)
    print(md)


if __name__ == "__main__":
    main()
