#!/usr/bin/env python3
"""П-42 v22 (TK-603): описательное сравнение «трейл тейкером (tl=0)» против «трейл лимиткой (tl=1|2)» по detail-файлам двух клеток.

Использование: python tools/p42/trail_limit_report.py --side long|short --days LO:HI --old <каталог клетки tl=0> --new <каталог клетки tl>0> [--json out.json]
Каталог клетки — `<run>/<id>/` с `<side>-detail.jsonl` (прогон с P42_DETAIL=1). Окно — сутки входа от начала счёта движка (`entry_day`, как в trades.csv).
Ничего не отбирает и не выбирает: суммы, средние на сделку, число сделок, трейлы (выставлено / исполнено / не исполнено и чем закрыто), задержка исполнения,
парное изменение P&L по ключу (монета, уровень, сигнал). Деньги: net_nf = gross − fees (без фандинга), net — как в движке (с фандингом).
"""
import argparse
import json
from collections import Counter

DAY_MS = 86_400_000
START_DAY = 14  # = START_DAY движка


def load(d, side, lo, hi):
    rows = []
    with open(f"{d}/{side}-detail.jsonl", encoding="utf8") as f:
        for line in f:
            r = json.loads(line)
            if lo <= r["entry_ms"] // DAY_MS - START_DAY < hi:
                rows.append(r)
    return rows


def lots(r):
    out = [{"kind": r.get("kind1", r["kind"]), "legs": r["legs"], "tl": r.get("tl")}]
    if "lot2" in r:
        l2 = r["lot2"]
        out.append({"kind": l2["kind"], "legs": l2["legs"], "tl": l2.get("tl")})
    return out


def key(r):
    return (r["sym"], r["level"], r["sig_ms"])


def nf(r):
    return r["gross"] - r["fees"]


def quant(v, q):
    if not v:
        return float("nan")
    v = sorted(v)
    return v[min(len(v) - 1, int(q * len(v)))]


def stats(rows):
    n = len(rows)
    s_nf = sum(nf(r) for r in rows)
    s_net = sum(r["net"] for r in rows)
    ls = [l for r in rows for l in lots(r)]
    trail = [l for l in ls if l["kind"] == "Trail"]
    placed = [l for l in ls if l["tl"] is not None]
    is_filled = lambda l: l["kind"] == "Trail" and l["legs"][-1][3]
    filled = [l for l in placed if is_filled(l)]
    unfilled = [l for l in placed if not is_filled(l)]
    delay = [l["legs"][-1][0] - l["tl"][0] for l in filled]
    return {
        "trades": n,
        "lots": len(ls),
        "sum_net_nf": s_nf,
        "mean_net_nf": s_nf / n if n else float("nan"),
        "sum_net": s_net,
        "gross": sum(r["gross"] for r in rows),
        "fees": sum(r["fees"] for r in rows),
        "funding": sum(r["funding"] for r in rows),
        "trail_exits": len(trail),
        "placed": len(placed),
        "filled": len(filled),
        "unfilled": len(unfilled),
        "unfilled_by": dict(Counter(l["kind"] for l in unfilled)),
        "delay_ms": {
            "n": len(delay),
            "<=100ms": sum(d <= 100 for d in delay),
            "<=1s": sum(100 < d <= 1000 for d in delay),
            "<=10s": sum(1000 < d <= 10_000 for d in delay),
            "<=60s": sum(10_000 < d <= 60_000 for d in delay),
            ">60s": sum(d > 60_000 for d in delay),
            "median_s": quant(delay, 0.5) / 1000,
            "p90_s": quant(delay, 0.9) / 1000,
            "max_s": (max(delay) / 1000) if delay else float("nan"),
        },
        "kinds": dict(Counter(l["kind"] for l in ls)),
    }


def pair(old, new):
    o = {key(r): r for r in old}
    n = {key(r): r for r in new}
    both = sorted(set(o) & set(n))
    only_o = [o[k] for k in o if k not in n]
    only_n = [n[k] for k in n if k not in o]
    d = [(nf(n[k]) - nf(o[k]), k) for k in both]

    def agg(ks):
        v = [nf(n[k]) - nf(o[k]) for k in ks]
        return {
            "n": len(v),
            "sum_d_nf": sum(v),
            "mean_d_nf": sum(v) / len(v) if v else float("nan"),
            "better": sum(x > 1e-9 for x in v),
            "worse": sum(x < -1e-9 for x in v),
            "same": sum(abs(x) <= 1e-9 for x in v),
            "d_gross": sum(n[k]["gross"] - o[k]["gross"] for k in ks),
            "d_fees": sum(n[k]["fees"] - o[k]["fees"] for k in ks),
            "d_funding": sum(n[k]["funding"] - o[k]["funding"] for k in ks),
        }

    had_trail = [k for k in both if any(l["kind"] == "Trail" for l in lots(o[k]))]
    ht = set(had_trail)
    rest = [k for k in both if k not in ht]
    all_filled = [k for k in had_trail if all(l["tl"] is None or (l["kind"] == "Trail" and l["legs"][-1][3]) for l in lots(n[k]))]
    af = set(all_filled)
    some_unfilled = [k for k in had_trail if k not in af]
    return {
        "matched": agg(both),
        "old_had_trail": agg(had_trail),
        "old_had_trail_new_all_filled": agg(all_filled),
        "old_had_trail_new_some_unfilled": agg(some_unfilled),
        "old_no_trail": agg(rest),
        "only_old": {"n": len(only_o), "sum_nf": sum(nf(r) for r in only_o)},
        "only_new": {"n": len(only_n), "sum_nf": sum(nf(r) for r in only_n)},
    }


def fmt(x, p=2):
    return f"{x:,.{p}f}".replace(",", " ") if isinstance(x, float) else str(x)


def lines(label, so, sn, pr):
    L = [f"**{label}**", "", "| | tl=0 (было) | tl>0 (стало) |", "|---|---:|---:|"]
    for name, k, p in [
        ("сделок", "trades", 0), ("Σ net_nf, $", "sum_net_nf", 2), ("среднее net_nf на сделку, $", "mean_net_nf", 3),
        ("Σ net с фандингом, $", "sum_net", 2), ("gross, $", "gross", 2), ("комиссии, $", "fees", 2), ("фандинг, $", "funding", 2),
        ("трейл-выходов (лотов)", "trail_exits", 0),
    ]:
        L.append(f"| {name} | {fmt(so[k], p) if p else so[k]} | {fmt(sn[k], p) if p else sn[k]} |")
    d = sn["delay_ms"]
    L += ["", f"Приказы трейла (tl>0): выставлено {sn['placed']}, исполнено {sn['filled']}, не исполнено {sn['unfilled']} (закрыто: {sn['unfilled_by']}).",
          f"Задержка исполнения: ≤100 мс {d['<=100ms']}, ≤1 с {d['<=1s']}, ≤10 с {d['<=10s']}, ≤60 с {d['<=60s']}, >60 с {d['>60s']}; медиана {fmt(d['median_s'], 1)} с, p90 {fmt(d['p90_s'], 1)} с, макс {fmt(d['max_s'], 0)} с.", ""]
    m = pr["matched"]
    L.append(f"Парно (одна и та же сделка: монета, уровень, сигнал): {m['n']} сделок, Σ Δnet_nf {fmt(m['sum_d_nf'])} $, среднее {fmt(m['mean_d_nf'], 3)} $; лучше {m['better']}, хуже {m['worse']}, без изменения {m['same']}; Δgross {fmt(m['d_gross'])}, Δкомиссии {fmt(m['d_fees'])}, Δфандинг {fmt(m['d_funding'])}.")
    for nm, k in [("были трейл-выходы, все приказы исполнены", "old_had_trail_new_all_filled"),
                  ("были трейл-выходы, есть неисполненный приказ", "old_had_trail_new_some_unfilled"),
                  ("трейл-выходов не было (разница — каскад портфеля)", "old_no_trail")]:
        a = pr[k]
        L.append(f"- {nm}: {a['n']} сделок, Σ Δ {fmt(a['sum_d_nf'])} $, среднее {fmt(a['mean_d_nf'], 3)} $; лучше {a['better']}, хуже {a['worse']}.")
    L.append(f"Только в «было»: {pr['only_old']['n']} сделок (Σ {fmt(pr['only_old']['sum_nf'])} $); только в «стало»: {pr['only_new']['n']} (Σ {fmt(pr['only_new']['sum_nf'])} $).")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--side", choices=("long", "short"), required=True)
    ap.add_argument("--days", required=True, help="LO:HI — сутки входа от начала счёта движка, [LO, HI)")
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--label", default="")
    ap.add_argument("--json")
    a = ap.parse_args()
    lo, hi = (int(x) for x in a.days.split(":"))
    old, new = load(a.old, a.side, lo, hi), load(a.new, a.side, lo, hi)
    so, sn, pr = stats(old), stats(new), pair(old, new)
    print(lines(a.label or f"{a.side}, сутки {lo}:{hi}", so, sn, pr))
    if a.json:
        with open(a.json, "w", encoding="utf8") as f:
            json.dump({"old": so, "new": sn, "pair": pr}, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
