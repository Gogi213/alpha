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


def orch(d, slots, disk_limit_mbps=136.0):
    """Слой оркестровки TK-048 в каталоге волны (units.txt, t-*.txt, tb-*.txt, merge.txt, samples.tsv): слоты P × стена."""
    uf = os.path.join(d, "units.txt")
    if not os.path.exists(uf):
        return None
    units = {}
    for l in open(uf):
        a = l.split()
        if len(a) == 4:
            units[(a[0], a[1])] = (float(a[2]), float(a[3]))
    t0 = min(v[0] for v in units.values())
    fin = [float(l.split()[1]) for l in open(os.path.join(d, "finish.txt"))] if os.path.exists(os.path.join(d, "finish.txt")) else []
    t1 = max([v[1] for v in units.values()] + fin)
    wall = t1 - t0
    uwall = sum(v[1] - v[0] for v in units.values())
    ucpu = ucpu_u = ucpu_s = 0.0
    urss = 0
    for f in glob.glob(os.path.join(d, "t-*.txt")):
        a = open(f).read().split()
        if len(a) >= 4:
            ucpu_u += float(a[1]); ucpu_s += float(a[2]); urss = max(urss, int(a[3]))
    ucpu = ucpu_u + ucpu_s
    tb = sum(float(open(f).read().split()[0]) for f in glob.glob(os.path.join(d, "tb-*.txt")))
    merge = sum(float(l.split()[2]) for l in open(os.path.join(d, "merge.txt"))) if os.path.exists(os.path.join(d, "merge.txt")) else 0.0
    # хвост по каждому слоту: последний финиш − предпоследний
    fs = sorted(fin)
    tail = fs[-1] - fs[-2] if len(fs) > 1 else 0.0
    # занятость слотов раз в секунду по единицам
    ev = sorted([(v[0], 1) for v in units.values()] + [(v[1], -1) for v in units.values()])
    cur = peak = 0
    for _, dl in ev:
        cur += dl; peak = max(peak, cur)
    res = dict(wall=wall, slots=slots, budget=wall * slots, units=len(units), unit_wall=uwall, unit_cpu_user=ucpu_u,
               unit_cpu_sys=ucpu_s, unit_cpu=ucpu, unit_rss_peak_kib=urss, b_phase_wall=tb, merge_wall=merge,
               tail_s=tail, peak_concurrency=peak)
    sf = os.path.join(d, "samples.tsv")
    if os.path.exists(sf):
        rows = []
        for l in open(sf):
            a = l.split()
            if len(a) >= 12:
                rows.append([float(x) for x in a])
        rows = [r for r in rows if t0 <= r[0] <= t1]
        if len(rows) > 2:
            f, l = rows[0], rows[-1]
            tot = sum(l[i] - f[i] for i in range(1, 6))
            names = ["user", "nice", "sys", "idle", "iowait"]
            res["machine_pct"] = {n: 100 * (l[1 + i] - f[1 + i]) / tot for i, n in enumerate(names)}
            dt = l[0] - f[0]
            res["disk"] = {}
            for nm, ix in (("sdb", 6), ("sda", 8)):
                mb = (l[ix] - f[ix]) * 512 / 1e6
                res["disk"][nm] = dict(MB=mb, MBps=mb / dt, busy_pct=100 * (l[ix + 1] - f[ix + 1]) / (dt * 1000),
                                       vs_limit_pct=100 * mb / dt / disk_limit_mbps)
            res["cache_gb_end"] = l[10] / 1048576
            res["running_avg"] = sum(r[11] for r in rows) / len(rows)
    return res


def orch_md(o, e):
    """Сводный водопад слотов: P × стена = ЦП бинарника по стадиям + ожидание + накладные + простой."""
    b = o["budget"]
    st = e["cpu"]; wt = e["wait"]
    stage_wall = sum(e["wall_by_stage"].values())
    cpu_stage = sum(st.values())
    lines = [f"### Водопад слотов: {o['slots']} слотов × {o['wall']:.0f} с = {b:,.0f} слот-с · единиц {o['units']}", "",
             "| корзина | слот-с | % |", "|---|---|---|"]
    def L(n, v):
        lines.append(f"| {n} | {v:,.0f} | {100*v/b:.1f} |")
    for k in STAGES:
        L(f"ЦП бинарника: {k}", st[k])
    L("ожидание чтения/ввода-вывода в стадиях (стена−ЦП)", sum(wt.values()))
    outside = o["unit_wall"] - stage_wall
    L("в единице вне стадий (запуск процесса, шелл, склейка вывода, итог)", outside)
    L("склейка (python) групп суток", o["merge_wall"])
    L("хвост суток B (нарезка/awk)", o["b_phase_wall"])
    idle = b - o["unit_wall"] - o["merge_wall"] - o["b_phase_wall"]
    L("простой слотов (нет готовой единицы / хвост)", idle)
    lines.append(f"| итого | {b:,.0f} | 100 |")
    lines.append("")
    lines.append(f"ЦП единиц по /usr/bin/time: user {o['unit_cpu_user']:,.0f} + sys {o['unit_cpu_sys']:,.0f} = {o['unit_cpu']:,.0f} с; "
                 f"ЦП в стадиях бинарника (процесс) {cpu_stage:,.0f} с; ЦП вне стадий {o['unit_cpu']-cpu_stage:,.0f} с; "
                 f"пик RSS единицы {o['unit_rss_peak_kib']/1048576:.1f} ГБ; хвост {o['tail_s']:.0f} с; пик единиц одновременно {o['peak_concurrency']}")
    if "machine_pct" in o:
        m = o["machine_pct"]
        lines.append("")
        lines.append("Машина за окно: " + ", ".join(f"{k} {v:.1f} %" for k, v in m.items()) + f"; единиц в среднем {o['running_avg']:.1f}; кэш страниц в конце {o['cache_gb_end']:.0f} ГБ")
        lines += ["", "| диск | ГБ | МБ/с | занят % времени | % от предела последовательного чтения |", "|---|---|---|---|---|"]
        for nm, v in o["disk"].items():
            lines.append(f"| {nm} | {v['MB']/1000:.1f} | {v['MBps']:.1f} | {v['busy_pct']:.0f} | {v['vs_limit_pct']:.0f} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--wall", type=float, default=0.0)
    ap.add_argument("--threads", type=int, default=16)
    ap.add_argument("--против")
    ap.add_argument("--slots", type=int, default=0)
    a = ap.parse_args()
    s = summarize(a.dir, a.wall, a.threads)
    print(md(s))
    res = {"main": s}
    if a.slots:
        o = orch(a.dir, a.slots)
        if o:
            s2 = summarize(a.dir, o["wall"], a.slots)
            print("\n" + orch_md(o, s2))
            res["orch"] = o
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
