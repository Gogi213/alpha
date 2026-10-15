#!/usr/bin/env python3
"""TK-244, В-9: независимая сверка metrics.jsonl с пересчётом из closes (критерий (б): ≥ 10 хешей, разные гипотезы). Не импортирует
metrics.py: свой разбор суток через datetime, statistics.stdev и свой скан максимума. Запускать на calc заданием alsched.
  verify_metrics.py --metrics metrics.jsonl [--routes routes.json] [--n 12] ИСТОЧНИК…   (ИСТОЧНИК как в build_metrics.py)
Печатает таблицу «страница / пересчёт»; код возврата 1, если хоть одна строка разошлась."""
import argparse
import datetime
import glob
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DAY = 86_400_000


def utc(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc)


def recheck(by_month):
    days, pnl, n, wins, over, tail = {}, 0.0, 0, 0, 0.0, 0.0
    for mo, trades in by_month.items():
        y, m = map(int, mo.split("-"))
        first = datetime.datetime(y, m, 1, tzinfo=datetime.timezone.utc)
        nxt = datetime.datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=datetime.timezone.utc)
        d = first
        while d < nxt:   # каждые календарные сутки месяца — в ряду, даже без сделок
            days.setdefault((mo, d.date()), 0.0)
            d += datetime.timedelta(days=1)
        tr = sorted((t, usd) for t, usd, *_ in trades)
        for t, usd in tr:
            k = (mo, utc(t).date())   # закрытие после конца месяца — продолжение суток своего месяца (как в сите TK-237)
            days[k] = days.get(k, 0.0) + usd
        n += len(tr)
        pnl += sum(u for _, u in tr)
        wins += sum(1 for _, u in tr if u > 0)
        if tr:
            start = int(first.timestamp() * 1000)
            end = int(nxt.timestamp() * 1000)
            acc, best, t_best, longest = 0.0, 0.0, start, 0
            for t, usd in tr:
                acc += usd
                if acc > best + 1e-9:
                    longest = max(longest, t - t_best)
                    best, t_best = acc, t
            over = max(over, longest / DAY)
            tail = max(tail, (max(end, tr[-1][0]) - t_best) / DAY)
    series = [days[k] for k in sorted(days)]
    sh = None
    if len(series) > 1:
        sd = statistics.stdev(series)
        sh = statistics.mean(series) / sd if sd > 1e-12 else None
    return {"n": n, "pnl": pnl, "win": wins / n if n else None, "sharpe": sh, "over": over, "tail": tail}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("sources", nargs="+")
    ap.add_argument("--metrics", required=True)
    ap.add_argument("--routes", default=os.path.join(HERE, "routes.json"))
    ap.add_argument("--n", type=int, default=12)
    a = ap.parse_args(argv)
    mets = [d for d in map(json.loads, filter(str.strip, open(a.metrics, encoding="utf-8"))) if d.get("n")]
    mets.sort(key=lambda d: d["hash"])
    pick = mets[:: max(1, len(mets) // (a.n - 3))][: a.n - 3]
    top = sorted((d for d in mets if d.get("sharpe") is not None), key=lambda d: -d["sharpe"])[:3]
    pick += [d for d in top if d not in pick]
    doc = json.load(open(a.routes, encoding="utf-8"))
    var = {}
    for h, ln in doc["lines"].items():
        if ln:
            form, _, st = ln.partition(" ")
            var[h] = {"*": f"{st}@{form}"}
    for h, mm in doc["monthly"].items():
        var[h] = {m: (lambda ln: f"{ln.partition(' ')[2]}@{ln.partition(' ')[0]}")(ln) for m, ln in mm.items() if ln}
    bad = 0
    print("хеш | n стр/пересч | pnl стр/пересч | шарп стр/пересч | винрейт стр/пересч | перехай стр/пересч | хвост стр/пересч | ок")
    for d in pick:
        h = d["hash"]
        got = {}
        for mo in d["by_month"]:
            for src in a.sources:
                dd, _, pat = src.rpartition("/")
                fs = sorted(glob.glob(os.path.join(dd, pat.replace("{m}", mo))))
                if not fs:
                    continue
                cl = json.load(open(fs[0], encoding="utf-8"))
                k = var[h].get(mo) or var[h].get("*")
                if k in cl:
                    got[mo] = cl[k].get(mo, {}).get("0", [])
                    break
        r = recheck(got)

        def eq(x, y, tol):
            return (x is None and y is None) or (x is not None and y is not None and abs(x - y) <= tol)
        ok = (d["n"] == r["n"] and eq(d["pnl"], r["pnl"], 0.02 * max(1, len(got))) and eq(d["sharpe"], r["sharpe"], 6e-5)
              and eq(d["winrate"], r["win"], 6e-5) and eq(d["overhigh_d"], r["over"], 0.011) and eq(d["tail_d"], r["tail"], 0.011)
              and set(got) == set(d["by_month"]))
        bad += not ok
        f = lambda x, p=4: "—" if x is None else f"{x:.{p}f}"
        print(f"{h[:12]} | {d['n']}/{r['n']} | {f(d['pnl'], 2)}/{f(r['pnl'], 2)} | {f(d['sharpe'])}/{f(r['sharpe'])} | "
              f"{f(d['winrate'])}/{f(r['win'])} | {f(d['overhigh_d'], 2)}/{f(r['over'], 2)} | {f(d['tail_d'], 2)}/{f(r['tail'], 2)} | "
              f"{'да' if ok else 'НЕТ'}")
    print(f"проверено {len(pick)} хешей из {len(mets)} с метриками; расхождений {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
