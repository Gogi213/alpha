#!/usr/bin/env python3
"""E32: какая оценка волатильности устойчивее (владелец 25.09: «может есть более устойчивая или робастная
волатильность, или комбинирование её типов»).

13 оценок по окну минутных свечей (сутки): RV по закрытиям (эталон), RV по 5 мин с усреднением сдвигов (устойчива к
шуму стакана), Паркинсон, Гарман–Класс, Роджерс–Сатчелл, Янг–Чжан (по размаху свечи), bipower и MedRV (устойчивы к
скачкам), RV только падений, EWMA (полураспад 60 мин), собственная RV монеты (остаток после BTC), смесь горизонтов
1 ч + 4 ч + сутки (HAR) и среднее рангов всех оценок.

Меры, по месяцам отдельно: (1) устойчивость уровня монеты — ρ месячных медиан (по дням) август↔сентябрь по монетам
пула и средняя ρ соседних дней; (2) прогноз на входах сделок — ρ оценки за сутки до входа с RV монеты и с худшим
ходом против позиции за 4 ч после входа; (3) ρ со стопом и $. Масштаб оценок не важен — все меры ранговые.

    python3 vol-estimators.py --trades study/loss-days-btc4h.csv \\
        --month август=epochs/e-aug/study/klines:epochs/e-aug:2026-08-01:2026-08-31 \\
        --month сентябрь=study/klines:epochs/e-archive,.:2026-09-01:2026-09-22 --drop TRXUSDT > study/vol-estimators.txt
"""
import argparse
import bisect
import csv
import datetime as dt
import math
import os
from collections import defaultdict

MIN_MS, DAY_MS = 60_000, 86_400_000
NAMES = [("rv_cc", "RV по закрытиям (эталон)"), ("rv_5m", "RV по 5 мин, среднее сдвигов"), ("park", "Паркинсон"),
         ("gk", "Гарман–Класс"), ("rs", "Роджерс–Сатчелл"), ("yz", "Янг–Чжан"), ("bpv", "bipower (без скачков)"),
         ("medrv", "MedRV (без скачков)"), ("down", "RV только падений"), ("ewma", "EWMA, полураспад 60 мин"),
         ("idio", "собственная RV монеты (без BTC)"), ("har", "смесь 1 ч + 4 ч + сутки (HAR)"), ("rank", "среднее рангов всех")]


def load(path):
    d = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                o, h, l, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
                if o > 0 and h > 0 and l > 0 and c > 0:
                    d[int(r["minute_ms"])] = (o, h, l, c)
    return d


def window(d, keys, m1, n):
    """n минутных свечей, закрытых до минуты m1 (не включая её)."""
    lo, hi = bisect.bisect_left(keys, m1 - n * MIN_MS), bisect.bisect_left(keys, m1)
    return [(k, d[k]) for k in keys[lo:hi]]


def rv(closes):
    return math.sqrt(sum(math.log(b / a) ** 2 for a, b in zip(closes, closes[1:])))


def estimators(bars, btc):
    """bars — [(минута, (o,h,l,c))] монеты; btc — {минута: close}. Все оценки — за окно целиком."""
    if len(bars) < 720:
        return None
    cl = [b[3] for _, b in bars]
    r = [math.log(b / a) for a, b in zip(cl, cl[1:])]
    n = len(r)
    e = {"rv_cc": math.sqrt(sum(x * x for x in r))}
    sub = []
    for k in range(5):
        c5 = cl[k::5]
        sub.append(sum(math.log(b / a) ** 2 for a, b in zip(c5, c5[1:])))
    e["rv_5m"] = math.sqrt(sum(sub) / len(sub))
    hl = [math.log(b[1] / b[2]) for _, b in bars]
    co = [math.log(b[3] / b[0]) for _, b in bars]
    e["park"] = math.sqrt(sum(x * x for x in hl) / (4 * math.log(2)))
    e["gk"] = math.sqrt(max(0.0, sum(0.5 * a * a - (2 * math.log(2) - 1) * c * c for a, c in zip(hl, co))))
    rsv = [math.log(b[1] / b[3]) * math.log(b[1] / b[0]) + math.log(b[2] / b[3]) * math.log(b[2] / b[0]) for _, b in bars]
    e["rs"] = math.sqrt(max(0.0, sum(rsv)))
    ov = [math.log(bars[i][1][0] / bars[i - 1][1][3]) for i in range(1, len(bars))]
    oc = co[1:]
    m_o, m_c = sum(ov) / len(ov), sum(oc) / len(oc)
    var_o = sum((x - m_o) ** 2 for x in ov) / (len(ov) - 1)
    var_c = sum((x - m_c) ** 2 for x in oc) / (len(oc) - 1)
    kk = 0.34 / (1.34 + (len(ov) + 1) / (len(ov) - 1))
    e["yz"] = math.sqrt(max(0.0, len(ov) * (var_o + kk * var_c + (1 - kk) * sum(rsv[1:]) / len(ov))))
    a = [abs(x) for x in r]
    e["bpv"] = math.sqrt(math.pi / 2 * sum(x * y for x, y in zip(a, a[1:])))
    med = [sorted(a[i - 1:i + 2])[1] ** 2 for i in range(1, n - 1)]
    e["medrv"] = math.sqrt(math.pi / (6 - 4 * math.sqrt(3) + math.pi) * n / (n - 2) * sum(med))
    e["down"] = math.sqrt(sum(x * x for x in r if x < 0))
    lam = 0.5 ** (1 / 60)
    v = sum(x * x for x in r[:60]) / 60
    for x in r[60:]:
        v = lam * v + (1 - lam) * x * x
    e["ewma"] = math.sqrt(v * n)
    mins = [m for m, _ in bars]
    pairs = [(x, math.log(btc[m1] / btc[m0])) for (m0, m1), x in zip(zip(mins, mins[1:]), r) if m0 in btc and m1 in btc]
    if len(pairs) > n // 2:
        sb = sum(b * b for _, b in pairs)
        beta = sum(x * b for x, b in pairs) / sb if sb else 0.0
        e["idio"] = math.sqrt(sum((x - beta * b) ** 2 for x, b in pairs) * n / len(pairs))
    else:
        e["idio"] = None
    e["har"] = (rv(cl[-61:]) * math.sqrt(24) + rv(cl[-241:]) * math.sqrt(6) + e["rv_cc"]) / 3
    return e


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
    if len(p) < 10:
        return None
    rx, ry = ranks([q[0] for q in p]), ranks([q[1] for q in p])
    n = len(p)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else None


def add_rank(rows):
    """Среднее рангов всех оценок (кроме самой) — по строкам одного набора."""
    keys = [k for k, _ in NAMES if k != "rank"]
    cols = {k: ranks([r[k] if r.get(k) is not None else -1 for r in rows]) for k in keys}
    for i, r in enumerate(rows):
        r["rank"] = sum(cols[k][i] for k in keys) / len(keys)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True)
    ap.add_argument("--month", action="append", required=True, help="имя=каталог_свечей:дом[,дом]:с:по")
    ap.add_argument("--drop", default="")
    a = ap.parse_args()
    drop = set(x for x in a.drop.split(",") if x)
    months = {}
    for s in a.month:
        name, rest = s.split("=", 1)
        kdir, homes, d0, d1 = rest.split(":")
        months[name] = (kdir, homes.split(","), d0, d1)
    trades = defaultdict(list)
    with open(a.trades, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            trades[r["month"]].append(r)

    daily = {}      # месяц → {монета: {оценка: [по дням]}}
    daylev = {}     # месяц → [(день, {монета: оценки})]
    entry = {}      # месяц → [строки сделок с оценками и исходами]
    for name, (kdir, homes, d0, d1) in months.items():
        btc = {}
        for h in homes:
            btc.update({m: b[3] for m, b in load(os.path.join(h, "study", "regime", "ref-BTCUSDT-1m.csv")).items()})
        syms = sorted(f[4:-7] for f in os.listdir(kdir) if f.startswith("ref-") and f.endswith("-1m.csv"))
        syms = [s for s in syms if s not in drop and s != "BTCUSDT"]
        day0 = int(dt.datetime.fromisoformat(d0).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
        day1 = int(dt.datetime.fromisoformat(d1).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
        per_coin, per_day = defaultdict(lambda: defaultdict(list)), defaultdict(dict)
        cache = {}
        for s in syms:
            d = load(os.path.join(kdir, f"ref-{s}-1m.csv"))
            keys = sorted(d)
            cache[s] = (d, keys)
            for day in range(day0, day1 + 1, DAY_MS):
                e = estimators(window(d, keys, day + DAY_MS, 1440), btc)
                if e:
                    per_day[day][s] = e
        for day, coins in per_day.items():
            rows = list(coins.values())
            add_rank(rows)
            for s, e in coins.items():
                for k, _ in NAMES:
                    if e.get(k) is not None:
                        per_coin[s][k].append(e[k])
        daily[name], daylev[name] = per_coin, sorted(per_day.items())
        rows = []
        for t in trades.get(name, []):
            if t["symbol"] not in cache:
                continue
            d, keys = cache[t["symbol"]]
            m = int(t["t0_ns"]) // 1_000_000 // MIN_MS * MIN_MS
            e = estimators(window(d, keys, m, 1440), btc)
            fwd = window(d, keys, m + 241 * MIN_MS, 241)
            if not e or len(fwd) < 120:
                continue
            c0 = d[keys[bisect.bisect_left(keys, m) - 1]][3]
            e["fwd_rv"] = rv([b[3] for _, b in fwd])
            e["fwd_mae"] = -(min(b[2] for _, b in fwd) / c0 - 1)
            e["stop"] = 1.0 if t["reason"] == "stop" else 0.0
            e["pnl"] = float(t["pnl_usd"])
            rows.append(e)
        add_rank(rows)
        entry[name] = rows
        print(f"{name}: монет {len(per_coin)}, дней {len(per_day)}, сделок с оценками {len(rows)}", flush=True)

    mnames = list(months)
    print("\nоценка | устойч. месяц→месяц | соседние дни (" + " / ".join(mnames) + ") | прогноз RV 4 ч (" + " / ".join(mnames)
          + ") | прогноз худшего хода 4 ч | со стопом | с $")
    base = {}
    table = []
    for k, label in NAMES:
        coins = [s for s in daily[mnames[0]] if s in daily[mnames[1]] and daily[mnames[0]][s].get(k) and daily[mnames[1]][s].get(k)]
        med = lambda v: sorted(v)[len(v) // 2]
        pers = spearman([med(daily[mnames[0]][s][k]) for s in coins], [med(daily[mnames[1]][s][k]) for s in coins])
        dd = []
        for mn in mnames:
            vals = []
            for (_, c1), (_, c2) in zip(daylev[mn], daylev[mn][1:]):
                common = [s for s in c1 if s in c2 and c1[s].get(k) is not None and c2[s].get(k) is not None]
                x = spearman([c1[s][k] for s in common], [c2[s][k] for s in common])
                if x is not None:
                    vals.append(x)
            dd.append(sum(vals) / len(vals) if vals else None)
        fr = [spearman([r[k] for r in entry[mn]], [r["fwd_rv"] for r in entry[mn]]) for mn in mnames]
        fm = [spearman([r[k] for r in entry[mn]], [r["fwd_mae"] for r in entry[mn]]) for mn in mnames]
        st_ = [spearman([r[k] for r in entry[mn]], [r["stop"] for r in entry[mn]]) for mn in mnames]
        pn = [spearman([r[k] for r in entry[mn]], [r["pnl"] for r in entry[mn]]) for mn in mnames]
        if k == "rv_cc":
            base = {"pers": pers, "dd": dd, "fr": fr}
        f2 = lambda v: "—" if v is None else f"{v:+.2f}"
        better = k != "rv_cc" and pers is not None and pers > base["pers"] and all(
            x is not None and y is not None and x > y for x, y in zip(fr, base["fr"]))
        table.append(f"{label:<32} | {f2(pers)} | {' / '.join(f2(x) for x in dd)} | {' / '.join(f2(x) for x in fr)} | "
                     f"{' / '.join(f2(x) for x in fm)} | {' / '.join(f2(x) for x in st_)} | {' / '.join(f2(x) for x in pn)}"
                     + ("  ← устойчивее и точнее эталона в обоих" if better else ""))
    print("\n".join(table))


if __name__ == "__main__":
    main()
