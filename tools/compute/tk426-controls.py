#!/usr/bin/env python3
"""TK-426 (протокол v2 §4): контроли C1 для событий пампа/дампа. Без σ24ч в подборе (σ — столбец-справка).
Использование: tk426-controls.py <pump-events.csv> <корень свечей 1м> <выход-окна.csv> <ГГГГ-ММ> [--confirmed файл.csv(колонка id)] [--per 3] [--diag diag.json]
C1 события = та же монета, та же длительность (смещения pre/peak/end от старта те же), время суток старта ±60 мин, та же терция хода BTC
(по ходу события: BTC close на пике / open на старте, bps, знак события; границы терций — по всем стартам месяца на сетке 5 мин для этой длины ноги
на BTC того же рынка), окно не пересекает окна ЛЮБОГО события этой монеты (из полного файла) ±60 мин, доля минут со свечой в [t_pre,t_end] не ниже, чем у самого события (не назначаемое число), на старте/пике/конце есть.
Выбор: детерминированный (хеш id+старт), без пересечений между контролями. Нет 3 → «не подобран» (в окна идут найденные, диагностика в --diag).
Выход — окна в формате tk422-features (+ роль, event_id, btc_ret_bps_leg, btc_tercile, sigma24_bps, sigma24_quint). Только ядро Python."""
import csv, gzip, hashlib, json, math, os, sys
from bisect import bisect_left, bisect_right

BTC = {"bybit-linear": "BTCUSDT", "bybit-spot": "BTCUSDT", "gate-futures_usdt": "BTC_USDT", "gate-spot": "BTC_USDT"}
MIN = 60000
GRID_MIN = 5
HOUR_TOL = 60       # мин, время суток старта (назначено протоколом v2: ±1 ч)
GUARD = 60          # мин, зазор вокруг окон событий той же монеты
SIG_LB = 1440


def load(path, t0, t1):
    """минута (индекс t//MIN) → (o, h, l, c); только валидные свечи"""
    S = {}
    with gzip.open(path, "rt") as f:
        f.readline()
        for line in f:
            p = line.split(",", 6)
            ti = int(p[0])
            if ti < t0 or ti >= t1:
                continue
            o, h, l, c = float(p[1]), float(p[2]), float(p[3]), float(p[4])
            if c <= 0 or l <= 0 or h <= 0 or o <= 0:
                continue
            S[ti // MIN] = (o, h, l, c)
    return S


def btc_ret(B, ms, leg, sg):
    a, b = B.get(ms), B.get(ms + leg)
    if a is None or b is None:
        return None
    return sg * (b[3] / a[0] - 1) * 1e4


def sigma24(S, ms):
    r, prev = [], None
    for m in range(ms - SIG_LB, ms):
        x = S.get(m)
        if x is None:
            prev = None
            continue
        if prev is not None:
            r.append(math.log(x[3] / prev))
        prev = x[3]
    if len(r) < SIG_LB // 2:
        return None
    mu = sum(r) / len(r)
    return math.sqrt(sum((v - mu) ** 2 for v in r) / len(r)) * 1e4


def quantile(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def main():
    a = sys.argv[1:]
    ev_path, root, out_path, ym = a[:4]
    opt = {a[i]: a[i + 1] for i in range(4, len(a) - 1, 2)}
    per = int(opt.get("--per", 3))
    ev = list(csv.DictReader(open(ev_path, encoding="utf-8")))
    ev = [r for r in ev if r["месяц"] == ym]
    conf = None
    if "--confirmed" in opt:
        conf = {r["id"] for r in csv.DictReader(open(opt["--confirmed"], encoding="utf-8"))}
    y, m = map(int, ym.split("-"))
    import calendar, datetime
    d0 = int(datetime.datetime(y, m, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
    d1 = d0 + calendar.monthrange(y, m)[1] * 86400000
    lo_ms, hi_ms = d0 - 2 * 86400000, d1 + 86400000
    by_mk = {}
    for r in ev:
        by_mk.setdefault(r["биржа"] + "-" + r["рынок"], []).append(r)
    rows, diag = [], {"не_подобрано": [], "подобрано": {}}
    for sub, E in sorted(by_mk.items()):
        B = load(os.path.join(root, sub, BTC[sub] + ".csv.gz"), lo_ms, hi_ms)
        cuts = {}
        coins = {}
        for r in E:
            coins.setdefault(r["монета"], []).append(r)
        for sym, L in sorted(coins.items()):
            S = load(os.path.join(root, sub, sym + ".csv.gz"), lo_ms, hi_ms)
            busy = sorted((int(r["t_pre_ms"]) // MIN - GUARD, int(r["t_end_ms"]) // MIN + GUARD) for r in L)
            bs = [b[0] for b in busy]
            sel = [r for r in L if conf is None or r["id"] in conf]
            for r in sel:
                tp, ts, tk, te = (int(r[k]) // MIN for k in ("t_pre_ms", "t_start_ms", "t_peak_ms", "t_end_ms"))
                sg = 1 if r["знак"] == "+" else -1
                leg = tk - ts
                if leg not in cuts:
                    v = [btc_ret(B, m_, leg, 1) for m_ in range(d0 // MIN, d1 // MIN - (te - ts), GRID_MIN)]
                    v = [x for x in v if x is not None]
                    cuts[leg] = v
                # терции по ориентированному ходу: для минуса знак меняется → считаем на ориентированных значениях
                ori = [sg * x for x in cuts[leg]]
                c1, c2 = quantile(ori, 1 / 3), quantile(ori, 2 / 3)

                def terc(x):
                    return 0 if x < c1 else (1 if x < c2 else 2)
                bev = btc_ret(B, ts, leg, sg)
                tev = terc(bev) if bev is not None else None
                tod = ts % 1440
                frac_ev = sum(1 for k in range(tp, te + 1) if k in S) / (te - tp + 1)
                cand = []
                first = d0 // MIN
                for day in range(calendar.monthrange(y, m)[1] + 1):
                    for dm in range(-HOUR_TOL, HOUR_TOL + 1, GRID_MIN):
                        s = first + day * 1440 + (tod + dm) % 1440 if False else first + day * 1440 + ((tod - first % 1440 + dm) % 1440)
                        pre_s, end_s = s - (ts - tp), s + (te - ts)
                        if pre_s * MIN < d0 or end_s * MIN >= d1:
                            continue
                        i = bisect_right(bs, end_s) - 1
                        if any(not (end_s < b0 or pre_s > b1) for b0, b1 in busy):
                            continue
                        if s not in S or s + leg not in S or end_s not in S:
                            continue
                        n = sum(1 for k in range(pre_s, end_s + 1) if k in S)
                        if n < frac_ev * (end_s - pre_s + 1):
                            continue
                        bt = btc_ret(B, s, leg, sg)
                        if bt is None or tev is None or terc(bt) != tev:
                            continue
                        cand.append((hashlib.md5(("%s|%d" % (r["id"], s)).encode()).hexdigest(), s, bt))
                cand.sort()
                got = []
                for _, s, bt in cand:
                    pre_s, end_s = s - (ts - tp), s + (te - ts)
                    if all(end_s + GUARD < g[0] or pre_s - GUARD > g[1] for g in [(x[1] - (ts - tp), x[1] + (te - ts)) for x in got]):
                        got.append((None, s, bt))
                        if len(got) == per:
                            break
                se = sigma24(S, ts)
                rows.append(dict(id=r["id"], биржа=r["биржа"], рынок=r["рынок"], инструмент=sym, t_start=ts * MIN, t_peak=tk * MIN,
                                 t_end=te * MIN, t_pre=tp * MIN, знак=r["знак"], роль="event", event_id=r["id"],
                                 btc_ret_bps_leg=None if bev is None else round(bev, 2), btc_tercile=tev,
                                 sigma24_bps=None if se is None else round(se, 3)))
                for j, (_, s, bt) in enumerate(got, 1):
                    sc = sigma24(S, s)
                    rows.append(dict(id="C1-%s-%d" % (r["id"], j), биржа=r["биржа"], рынок=r["рынок"], инструмент=sym, t_start=s * MIN,
                                     t_peak=(s + leg) * MIN, t_end=(s + te - ts) * MIN, t_pre=(s - (ts - tp)) * MIN, знак=r["знак"],
                                     роль="C1", event_id=r["id"], btc_ret_bps_leg=round(bt, 2), btc_tercile=tev,
                                     sigma24_bps=None if sc is None else round(sc, 3)))
                diag["подобрано"][r["id"]] = len(got)
                if len(got) < per:
                    diag["не_подобрано"].append(r["id"])
    # квинтиль σ по рынку среди событий+контролей (справка)
    for sub in by_mk:
        ex, mk = sub.split("-", 1)
        R = [x for x in rows if x["биржа"] == ex and x["рынок"] == mk and x["sigma24_bps"] is not None]
        v = [x["sigma24_bps"] for x in R]
        cuts5 = [quantile(v, k / 5) for k in range(1, 5)] if v else []
        for x in rows:
            if x["биржа"] == ex and x["рынок"] == mk:
                x["sigma24_quint"] = None if x["sigma24_bps"] is None else bisect_right(cuts5, x["sigma24_bps"]) + 1
    cols = ["id", "биржа", "рынок", "инструмент", "t_start", "t_peak", "t_end", "t_pre", "знак", "роль", "event_id", "btc_ret_bps_leg",
            "btc_tercile", "sigma24_bps", "sigma24_quint"]
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    ne = sum(1 for x in rows if x["роль"] == "event")
    diag["событий"] = ne
    diag["контролей"] = len(rows) - ne
    diag["доля_не_подобрано"] = round(len(diag["не_подобрано"]) / ne, 4) if ne else None
    if "--diag" in opt:
        json.dump(diag, open(opt["--diag"], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("события %d, контролей %d, не подобрано (<%d) %d" % (ne, len(rows) - ne, per, len(diag["не_подобрано"])))


if __name__ == "__main__":
    main()
