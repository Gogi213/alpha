#!/usr/bin/env python3
"""TK-052 (В-187): водопад поток-секунд волны из строк ALPHA_E2E (*.jsonl в каталоге волны).
  e2e-report.py <каталог волны> [--wall СЕК] [--threads N] [--против <каталог другой волны>]
Корзины: ЦП по стадиям (cpu процесса), ожидание = стена − ЦП внутри стадии, остаток = N×стена − сумма стадий.
Вывод: markdown в stdout + e2e-report.json в каталоге волны."""
import argparse, glob, json, os, sys
from collections import defaultdict

STAGES = ["touches_load", "events", "windows", "prep", "drive"]


def load(d):
    rows = []
    for f in sorted(glob.glob(os.path.join(d, "*.jsonl"))):
        for line in open(f, encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    return rows


def summarize(d, wall, threads):
    rows = load(d)
    st = [r for r in rows if r.get("t") == "stage"]
    runs = [r for r in rows if r.get("t") == "run"]
    days = [r for r in rows if r.get("t") == "day"]
    cpu = defaultdict(float)
    wt = defaultdict(float)
    rbytes = defaultdict(float)
    for r in st:
        k = r["stage"]
        if k == "day_total":
            continue
        cpu[k] += r["cpu"]
        wt[k] += r["wall"]
        rbytes[k] += r["rchar"]
    write = sum(r.get("write_wall", 0.0) for r in st if r["stage"] == "drive")
    cpu["drive_write_wall"] = write
    budget = wall * threads if wall else 0.0
    # ожидание стадии = стена − ЦП процесса (в стадии drive ядер несколько: ЦП может превышать стену)
    wait = {k: max(wt[k] - cpu[k], 0.0) for k in STAGES}
    used = sum(cpu[k] for k in STAGES) + sum(wait.values())
    top = defaultdict(lambda: [0.0, 0.0])
    for r in st:
        if r["stage"] == "day_total":
            top[(r["sym"], r["day"])] = [r["cpu"], max(r["wall"] - r["cpu"], 0.0)]
    topcpu = sorted(top.items(), key=lambda kv: -kv[1][0])[:15]
    topwait = sorted(top.items(), key=lambda kv: -kv[1][1])[:15]
    return dict(
        dir=d, runs=len(runs), days=len(days), budget=budget, wall=wall, threads=threads,
        cpu={k: cpu[k] for k in STAGES}, wait=wait, wall_by_stage={k: wt[k] for k in STAGES},
        read_bytes={k: rbytes[k] for k in STAGES}, write_wall=write,
        unaccounted=(budget - used) if budget else None, used=used,
        rss_peak_kib=max([r.get("rss_peak_kib", 0) for r in days] or [0]),
        top_cpu=[[f"{s} {d}", v[0], v[1]] for (s, d), v in topcpu],
        top_wait=[[f"{s} {d}", v[0], v[1]] for (s, d), v in topwait],
    )


def md(s):
    out = [f"### Водопад: {s['dir']}", ""]
    out.append(f"запусков {s['runs']} · монето-суток {s['days']} · пик RSS {s['rss_peak_kib']/1048576:.1f} ГБ")
    b = s["budget"]
    out += ["", "| корзина | сек | % бюджета |", "|---|---|---|"]
    def line(name, v):
        out.append(f"| {name} | {v:,.0f} | {100*v/b:.1f} |" if b else f"| {name} | {v:,.0f} | — |")
    for k in STAGES:
        line(f"ЦП {k}", s["cpu"][k])
    for k in STAGES:
        line(f"ожидание {k} (стена−ЦП)", s["wait"][k])
    line("в т.ч. запись вывода (стена в drive)", s["write_wall"])
    if b:
        line("неразобранное (простой/оркестровка/хвост)", s["unaccounted"])
        out.append(f"| итого бюджет {s['threads']}×{s['wall']:.0f} с | {b:,.0f} | 100 |")
    out += ["", "Чтение (rchar, ГБ) по стадиям: " + ", ".join(f"{k} {s['read_bytes'][k]/1e9:.1f}" for k in STAGES), ""]
    out += ["Топ-15 монето-суток по ЦП (сутки целиком):", ""]
    out += [f"- {n}: ЦП {c:.0f} с, ожидание {w:.0f} с" for n, c, w in s["top_cpu"]]
    out += ["", "Топ-15 по ожиданию:", ""]
    out += [f"- {n}: ожидание {w:.0f} с, ЦП {c:.0f} с" for n, c, w in s["top_wait"]]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--wall", type=float, default=0.0)
    ap.add_argument("--threads", type=int, default=16)
    ap.add_argument("--против")
    a = ap.parse_args()
    s = summarize(a.dir, a.wall, a.threads)
    print(md(s))
    res = {"main": s}
    if a.против:
        o = summarize(a.против, a.wall, a.threads)
        print("\n### Сравнение (основная − другая), сек\n\n| корзина | Δ |\n|---|---|")
        for k in STAGES:
            print(f"| ЦП {k} | {s['cpu'][k]-o['cpu'][k]:+,.0f} |")
            print(f"| ожидание {k} | {s['wait'][k]-o['wait'][k]:+,.0f} |")
        res["against"] = o
    json.dump(res, open(os.path.join(a.dir, "e2e-report.json"), "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    sys.exit(main())
