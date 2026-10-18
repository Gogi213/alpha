#!/usr/bin/env python3
"""TK-279 (П-17 §0 п.3): пороги T_m(q) по касаниям B1 СВОЕГО месяца — Г-101: q-квантиль btc_ret_4h_bps; Г-135: q-квантиль
swept_lots·10⁴/size_at_touch (бп, ключ набора swept_min=<бп>: `swept_lots·10⁴ ≥ q·size_at_touch`, порог — потолок квантиля до целого).
Г-135: касание подхода B1 = первая строка touches-<SYM>.csv (side=bid, price_tick из signals.csv) с start_ms ≥ t0; нет строки — вне популяции.

Касания B1 — ключи b1keys-cap0.csv (month,symbol,t0_ms,exit_ms; те же, что у tk202-thresholds.py); значение — колонка
btc_ret_4h_bps файла режима `<t46>/<мес>/home/alpha/epochs/e-<мес>/study/regime/<сутки>.csv` на минуте t0 (floor до минуты,
как `touch_contexts`). Квантиль — ближайший ранг без интерполяции (как tk202-thresholds.py), q ∈ {5,10,20,33,50,67,80,90,95},
округление до 2 знаков (как `--set`). Нет значения — касание вне популяции (счётчик).

Запуск: p17-btc4h-thresholds.py <b1keys.csv> <корень tk046 /data/tk046> <выход.csv> [<выход.json> [<pool /data/tk064/pool>]]
Выход CSV: hyp,month,axis,q,threshold,n — Г-101 (axis=btc4h), Г-135 (axis=swept_pct_of_size_bp, если задан pool); Г-81 — проход меры в Rust."""
import csv, datetime, json, math, sys
from collections import defaultdict

MON = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct"]
QS = (5, 10, 20, 33, 50, 67, 80, 90, 95)


def rank(a, p):
    a = sorted(a)
    return a[min(len(a) - 1, max(0, math.ceil(p / 100 * len(a)) - 1))]


def day_of(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")


def swept_ratios(pool, t46, mon, day, sym_t0):
    """swept_lots·10⁴/size_at_touch касаний B1 суток: ([значения], число без строки касания)."""
    sig = {}
    for sub in ("signals", "signals-retry"):
        try:
            with open(f"{pool}/m-{mon}/{sub}/{day}/t-bid-btc4h-q1/signals.csv", encoding="utf-8") as ih:
                for r in csv.DictReader(l for l in ih if not l.startswith("#")):
                    sig[(r["symbol"], int(r["t0_ns"]) // 1000000)] = int(float(r["price_tick"]))
        except FileNotFoundError:
            pass
    tou = {}
    out, miss = [], 0
    for s_, t in sym_t0:
        pt = sig.get((s_, t))
        if pt is None:
            miss += 1
            continue
        if s_ not in tou:
            rows_ = defaultdict(list)
            try:
                with open(f"{t46}/{mon}/home/alpha/epochs/e-{mon}/study/approaches/D20/{day}/touches-{s_}.csv", encoding="utf-8") as fh:
                    for r in csv.DictReader(fh):
                        if r["side"] == "bid":
                            rows_[int(r["price_tick"])].append((int(r["start_ms"]), float(r["swept_lots"] or 0), float(r["size_at_touch"] or 0)))
            except FileNotFoundError:
                pass
            tou[s_] = rows_
        cand = sorted(x for x in tou[s_].get(pt, []) if x[0] >= t)
        if not cand or cand[0][2] <= 0:
            miss += 1
            continue
        out.append(cand[0][1] * 10000 / cand[0][2])
    return out, miss


def main():
    keys_f, t46, out_csv = sys.argv[1:4]
    out_json = sys.argv[4] if len(sys.argv) > 4 else None
    pool = sys.argv[5] if len(sys.argv) > 5 else None
    keys = defaultdict(list)
    for r in csv.DictReader(open(keys_f, encoding="utf-8")):
        keys[r["month"]].append((r["symbol"], int(r["t0_ms"])))
    rows, js = [], {}
    for mo in sorted(keys):
        mon = MON[int(mo[5:]) - 1]
        byday, byday_syms = defaultdict(list), defaultdict(list)
        for s_, t in keys[mo]:
            byday[day_of(t)].append(t)
            byday_syms[day_of(t)].append((s_, t))
        vals, miss, sw, swmiss = [], 0, [], 0
        for day, ts in sorted(byday.items()):
            rg = {}
            try:
                with open(f"{t46}/{mon}/home/alpha/epochs/e-{mon}/study/regime/{day}.csv", encoding="utf-8") as fh:
                    for r in csv.DictReader(fh):
                        rg[int(r["minute_ms"])] = r.get("btc_ret_4h_bps", "")
            except FileNotFoundError:
                pass
            for t in ts:
                v = rg.get(t - t % 60000, "")
                if v != "":
                    vals.append(float(v))
                else:
                    miss += 1
            if pool:
                r_, m_ = swept_ratios(pool, t46, mon, day, byday_syms[day])
                sw += r_
                swmiss += m_
        if not vals:
            continue
        js[mo] = {"n": len(vals), "miss": miss, "q": {}}
        if sw:
            js[mo]["swept"] = {"n": len(sw), "miss": swmiss, "q": {}}
            for q in QS:
                thr = math.ceil(rank(sw, q))
                js[mo]["swept"]["q"][str(q)] = thr
                rows.append(("Г-135", mo, "swept_pct_of_size_bp", q, thr, len(sw)))
        for q in QS:
            thr = round(rank(vals, q), 2)
            js[mo]["q"][str(q)] = thr
            rows.append(("Г-101", mo, "btc4h", q, thr, len(vals)))
    with open(out_csv, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(("hyp", "month", "axis", "q", "threshold", "n"))
        w.writerows(rows)
    if out_json:
        json.dump(js, open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({m: (v["n"], v["miss"], v.get("swept", {}).get("n"), v.get("swept", {}).get("miss")) for m, v in js.items()}, ensure_ascii=False))


main()
