#!/usr/bin/env python3
"""Повторы по ВСЕМ номерам подхода (владелец 27.09 ~04:30 через CEO: «разложи на все подходы. че только 1-2»).
Те же сделки главного варианта и та же связь сделка -> стена, что у tools/compute/t32-retries.py
(data/t32/main-trades.csv, data/t32/retries-link.csv); без подбора — печатается всё.

1) по номеру подхода на входе (approach_number_at_entry, только связанные сделки) 1, 2, 3, … до максимума,
   каждый месяц отдельно: n, $, доля в плюс, доля стопов; хвост, где в обоих месяцах n < 10, — одной строкой «N+»;
2) правила с KPI: «только N-й», «первые K», «с N-го и дальше», «все» — n, $, скользящий p90 / max до перехая
   (Каплан — Мейер, дней) и доля часов с ожиданием перехая > 5 сут (`frac_gt_h`, t ≤ 24.09 − 5 сут = 19.09,
   август — ожидание продолжается в сентябрь) — по tools/compute/kpi-newhigh.py (`--kpi-module`);
3) сделки без известного номера подхода (ambiguous / нет связи) — правило их не режет, отдельной строкой.

    python tools/compute/t32-retries-all.py --out data/t32/retries-all.json --summary data/t32/retries-all-summary.md
"""
import argparse
import importlib.util
import json
import os

MONTHS = ("aug", "sep")
H_DAYS = 5


def load_mod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def appr_num(t):
    lk = t["link"]
    if not lk or lk["status"] != "linked":
        return None
    try:
        return int(lk["approach_number_at_entry"])
    except (TypeError, ValueError):
        return None


def stats(ts):
    n = len(ts)
    if not n:
        return {"n": 0, "usd": 0.0, "win": None, "stop": None}
    return {"n": n, "usd": round(sum(t["pnl"] for t in ts), 2),
            "win": round(sum(1 for t in ts if t["pnl"] > 0) / n, 3),
            "stop": round(sum(1 for t in ts if t["reason"] == "stop") / n, 3)}


def kpi(kn, kept):
    closes = lambda m=None: [(t["t1"], t["pnl"]) for t in kept if m is None or t["month"] == m]
    roll = kn.rolling_kpi(closes(), h_days=H_DAYS)
    out = {}
    for m in MONTHS:
        mm = kn.month_metrics(closes(m), m)
        r = roll[m]
        d = lambda x: None if x is None else round(x / 24, 1)
        out[m] = {"n": mm["n"], "usd": round(mm["usd"], 2), "frac_gt_5d": r["frac_gt_h"], "n_hours": r["n_main"],
                  "p90_days": d(r["max"]["p90"]), "p90_censored": r["max"]["p90_censored"],
                  "max_days": d(r["max"]["max"]), "max_censored": r["max"]["max_censored"]}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trades", default="data/t32/main-trades.csv")
    ap.add_argument("--link", default="data/t32/retries-link.csv")
    ap.add_argument("--kpi-module", default="tools/compute/kpi-newhigh.py")
    ap.add_argument("--max-rule", type=int, default=6)
    ap.add_argument("--out", default="data/t32/retries-all.json")
    ap.add_argument("--summary", default="data/t32/retries-all-summary.md")
    a = ap.parse_args()

    rt = load_mod("tools/compute/t32-retries.py", "rt")
    kn = load_mod(a.kpi_module, "kn")
    trades = rt.load_trades(a.trades)
    link = rt.load_link(a.link)
    for t in trades:
        t["link"] = link.get((t["month"], t["sym"], t["t0"]))
        t["appr"] = appr_num(t)

    # 1) по номеру подхода
    nums = sorted({t["appr"] for t in trades if t["appr"] is not None})
    by_num = {n: {m: stats([t for t in trades if t["appr"] == n and t["month"] == m]) for m in MONTHS} for n in nums}
    tail = next((n for n in nums if all(all(by_num[k][m]["n"] < 10 for m in MONTHS) for k in nums if k >= n)), None)
    rows = [(str(n), by_num[n]) for n in nums if tail is None or n < tail]
    if tail is not None:
        rows.append((f"{tail}+", {m: stats([t for t in trades if t["appr"] is not None and t["appr"] >= tail
                                            and t["month"] == m]) for m in MONTHS}))
    unknown = {m: stats([t for t in trades if t["appr"] is None and t["month"] == m]) for m in MONTHS}

    # 2) правила (неизвестный номер — не режем)
    rules = []

    def rule(name, keep):
        kept = [t for t in trades if t["appr"] is None or keep(t["appr"])]
        rules.append({"rule": name, **kpi(kn, kept)})

    rule("все (база)", lambda n: True)
    for N in range(1, a.max_rule + 1):
        rule(f"только {N}-й", lambda n, N=N: n == N)
    for K in range(1, a.max_rule + 1):
        rule(f"первые {K}", lambda n, K=K: n <= K)
    for N in range(2, a.max_rule + 1):
        rule(f"с {N}-го и дальше", lambda n, N=N: n >= N)

    out = {"by_approach_number": {k: v for k, v in rows}, "tail_from": tail, "max_approach_number": max(nums),
           "unknown_number": unknown, "rules": rules, "n_rules": len(rules), "h_days": H_DAYS,
           "kpi_module": a.kpi_module, "n_trades": {m: sum(1 for t in trades if t["month"] == m) for m in MONTHS}}
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    pc = lambda x: "—" if x is None else f"{x * 100:.0f}%"
    L = ["# Повторы по всем номерам подхода (главный вариант, описание, не вердикт)", "",
         f"Сделок: август {out['n_trades']['aug']}, сентябрь {out['n_trades']['sep']}; максимум номера подхода — "
         f"{out['max_approach_number']}; хвост «{tail}+» — где в обоих месяцах n < 10.", "",
         "## 1. По номеру подхода на входе", "",
         "| подход | авг n | авг $ | авг в плюс | авг стопов | сен n | сен $ | сен в плюс | сен стопов |",
         "|---|---|---|---|---|---|---|---|---|"]
    for k, v in rows + [("без номера", unknown)]:
        L.append(f"| {k} | " + " | ".join(f"{v[m]['n']} | {v[m]['usd']:+.0f} | {pc(v[m]['win'])} | {pc(v[m]['stop'])}"
                                           for m in MONTHS) + " |")
    L += ["", f"## 2. Правила (без номера — не режутся; KPI: доля часов с ожиданием перехая > {H_DAYS} сут, "
              "p90 / max до перехая со скользящим стартом, дней; * — цензура; < 10 сделок — «сделок нет», 10–29 — описание)", "",
          "| правило | авг n | авг $ | авг доля > 5 сут | авг p90 / max | сен n | сен $ | сен доля > 5 сут | сен p90 / max |",
          "|---|---|---|---|---|---|---|---|---|"]
    st = lambda c: "*" if c else ""
    def cell(x):
        if x["n"] < 10:  # правило Судьи (П-07 3d3a5f6 п.4): < 10 сделок в месяце — «сделок нет»
            return f"{x['n']} | {x['usd']:+.0f} | сделок нет | —"
        mark = " (описание)" if x["n"] < 30 else ""
        return (f"{x['n']} | {x['usd']:+.0f} | {pc(x['frac_gt_5d'])}{mark} | "
                f"{x['p90_days']}{st(x['p90_censored'])} / {x['max_days']}{st(x['max_censored'])}")
    for r in rules:
        L.append(f"| {r['rule']} | " + " | ".join(cell(r[m]) for m in MONTHS) + " |")
    L += ["", f"В правилах номер без связи (август {unknown['aug']['n']}, сентябрь {unknown['sep']['n']}) не режется — поэтому "
              "«только 1-й» в августе на эти сделки больше строки «1» таблицы 1.",
          "", f"Просмотрено правил: {len(rules)} (все выписаны, без подбора). Числа — на тех же днях, что и главный "
              "вариант; «лучшее правило» из таблицы — максимум из просмотренных, не проверка."]
    with open(a.summary, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
