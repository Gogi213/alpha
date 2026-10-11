#!/usr/bin/env python3
"""E34: поведение стены в первые секунды касания как ранний сигнал стопа (Г-04 поглощение, Г-06 скорость подхода).

Сделке главного варианта (`loss-days.py --csv`) — её подход (`study/approaches/D20/<сутки>/approaches-<SYM>.csv`, сторона
bid, ближайший `arm_ms` ко входу в пределах 2 мин) и первое касание той же цены после входа, но до выхода
(`study/touches/<сутки>/touches-<SYM>.csv`, сутки входа и следующие). Признаки касания — как их пишет `lob touches`.
ρ Спирмена со стопом и $, $ по третям (границы по обоим месяцам), по месяцам отдельно. Чтение, не вердикт.

    python3 touch-signals.py --trades study/loss-days-btc4h.csv \\
        --homes август=epochs/e-aug --homes сентябрь=epochs/e-archive,. > study/touch-signals.txt
"""
import argparse
import csv
import datetime as dt
import os
from collections import defaultdict

FEATS = [("approach_1s", "скорость подхода за 1 с"), ("approach_10s", "скорость подхода за 10 с"),
         ("traded_1s", "проторговано в стену за 1 с"), ("traded_2s", "за 2 с"), ("traded_3s", "за 3 с"),
         ("traded_rel", "проторговано за касание к размеру стены"),
         ("held_1s", "стена удержала размер через 1 с, %"), ("held_5s", "через 5 с, %"), ("held_15s", "через 15 с, %"),
         ("held_60s", "через 60 с, %"), ("swept_rel", "снесено к размеру"), ("size_rel", "размер на касании к максимуму до"),
         ("frontrun_rel", "фронтран к размеру"), ("m_1s", "ход цены через 1 с"), ("m_10s", "через 10 с"), ("m_60s", "через 60 с"),
         ("sigma_60s_bps", "σ за 60 с"), ("sigma_600s_bps", "σ за 600 с"), ("mins_to_touch", "минут от входа до касания"),
         ("touched", "касание было (1/0)")]


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def read(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    rk = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            rk[order[k]] = (i + j) / 2
        i = j + 1
    return rk


def spearman(x, y):
    p = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]
    if len(p) < 20:
        return None
    rx, ry = ranks([q[0] for q in p]), ranks([q[1] for q in p])
    n = len(p)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True)
    ap.add_argument("--homes", action="append", required=True)
    a = ap.parse_args()
    homes = {s.split("=", 1)[0]: s.split("=", 1)[1].split(",") for s in a.homes}
    cache = {}

    def load(kind, h, day, sym):
        key = (kind, h, day, sym)
        if key not in cache:
            sub = os.path.join("approaches", "D20") if kind == "approaches" else "touches"
            cache[key] = read(os.path.join(h, "study", sub, day, f"{kind}-{sym}.csv"))
        return cache[key]

    # проход 1: подход каждой сделки (файлы подходов небольшие) и нужные цены касаний по (дом, сутки, монета)
    trades, need = [], defaultdict(set)
    miss = defaultdict(int)
    with open(a.trades, encoding="utf-8") as f:
        for t in csv.DictReader(f):
            m = t["month"]
            t0, t1 = int(t["t0_ns"]) // 1_000_000, int(t["t1_ns"]) // 1_000_000
            day = dt.datetime.fromtimestamp(t0 / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
            nxt = dt.datetime.fromtimestamp(t0 / 1000 + 86400, dt.timezone.utc).strftime("%Y-%m-%d")
            appr = None
            for h in homes[m]:
                cand = [x for x in load("approaches", h, day, t["symbol"]) if x["side"] == "bid" and abs(int(x["arm_ms"]) - t0) <= 120_000]
                if cand:
                    appr = min(cand, key=lambda x: abs(int(x["arm_ms"]) - t0))
                    break
            if appr is None:
                miss[m] += 1
                continue
            trades.append((m, t, t0, t1, appr, (day, nxt)))
            for h in homes[m]:
                for d in (day, nxt):
                    need[(h, d, t["symbol"])].add(appr["price_tick"])
    # проход 2: файлы касаний — один раз, только строки нужных цен (строка начинается с «сутки,bid,цена,»)
    touches = defaultdict(list)
    header = None
    for (h, d, sym), ticks in need.items():
        path = os.path.join(h, "study", "touches", d, f"touches-{sym}.csv")
        if not os.path.exists(path):
            continue
        prefixes = tuple(f"{d},bid,{tk}," for tk in ticks)
        with open(path, encoding="utf-8") as f:
            head = f.readline().rstrip("\r\n").split(",")
            header = header or head
            for line in f:
                if line.startswith(prefixes):
                    x = dict(zip(head, line.rstrip("\r\n").split(",")))
                    touches[(h, d, sym, x["price_tick"])].append(x)
    rows = defaultdict(list)
    for m, t, t0, t1, appr, days in trades:
        tick, touch = appr["price_tick"], None
        for h in homes[m]:
            for d in days:
                cand = [x for x in touches.get((h, d, t["symbol"], tick), []) if int(appr["arm_ms"]) <= int(x["start_ms"]) <= t1]
                if cand:
                    touch = min(cand, key=lambda x: int(x["start_ms"]))
                    break
            if touch:
                break
        r = {"stop": 1.0 if t["reason"] == "stop" else 0.0, "pnl": float(t["pnl_usd"]), "touched": 1.0 if touch else 0.0}
        if touch:
            size = fnum(touch["size_max_before"]) or fnum(touch["size_at_touch"]) or 0.0
            r.update({k: fnum(touch.get(k)) for k in ("approach_1s", "approach_10s", "traded_1s", "traded_2s", "traded_3s",
                                                      "m_1s", "m_10s", "m_60s", "sigma_60s_bps", "sigma_600s_bps")})
            for s_ in ("1s", "5s", "15s", "60s"):
                r[f"held_{s_}"] = fnum(touch.get(f"strength_held_{s_}_pct"))
            r["traded_rel"] = (fnum(touch["traded_during"]) or 0.0) / size if size else None
            r["swept_rel"] = (fnum(touch["swept_lots"]) or 0.0) / size if size else None
            r["frontrun_rel"] = (fnum(touch["frontrun_lots"]) or 0.0) / size if size else None
            r["size_rel"] = (fnum(touch["size_at_touch"]) or 0.0) / size if size else None
            r["mins_to_touch"] = (int(touch["start_ms"]) - t0) / 60000
        rows[m].append(r)
    months = list(rows)
    for m in months:
        n, tc = len(rows[m]), sum(r["touched"] for r in rows[m])
        st_t = [r for r in rows[m] if r["touched"]]
        print(f"{m}: сделок {n} (без подхода {miss[m]}), касание было у {tc:.0f}; стопов: с касанием "
              f"{sum(r['stop'] for r in st_t):.0f} из {len(st_t)}, без касания {sum(r['stop'] for r in rows[m] if not r['touched']):.0f} из {n - len(st_t)}")
    print("\nпризнак | " + " | ".join(f"{m}: ρ со стопом / ρ с $ / $ по третям (низ·сер·верх)" for m in months) + " | кандидат")
    for k, label in FEATS:
        vals = sorted(r[k] for m in months for r in rows[m] if r.get(k) is not None)
        if len(vals) < 30:
            continue
        lo, hi = vals[len(vals) // 3], vals[2 * len(vals) // 3]
        cells, rs = [], []
        for m in months:
            xs = [r.get(k) for r in rows[m]]
            rst = spearman(xs, [r["stop"] for r in rows[m]])
            rp = spearman(xs, [r["pnl"] for r in rows[m]])
            t = [0.0, 0.0, 0.0]
            for r in rows[m]:
                if r.get(k) is not None:
                    t[0 if r[k] <= lo else (1 if r[k] <= hi else 2)] += r["pnl"]
            rs.append(rst)
            f2 = lambda v: "—" if v is None else f"{v:+.2f}"
            cells.append(f"{f2(rst)} / {f2(rp)} / {t[0]:+.0f}·{t[1]:+.0f}·{t[2]:+.0f}")
        cand = all(x is not None and abs(x) >= 0.10 for x in rs) and len({x > 0 for x in rs}) == 1
        print(f"{label} (трети ≤{lo:.2f} / ≤{hi:.2f}) | " + " | ".join(cells) + (" | ДА" if cand else " | нет"))


if __name__ == "__main__":
    main()
