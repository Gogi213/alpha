#!/usr/bin/env python3
"""TK-326: разбор убытка спота S0 (TK-313) из готовых rounds.csv + signals.csv, без нового прохода (В-213).
Издержки: gross = net_bps + 1.26 + leg (модель фьюча, leg 3.15 для stop/deadline, иначе 1.26) — как tk313/tk324.
(а) фьюч-модель; (б) публичный спот 10/10; (в) тариф В-47: вход maker 5.625, выход taker 6.75 (stop/deadline) / maker 5.625.
Выход: markdown + json в argv[1] (префикс). Запуск: на calc, под systemd-run."""
import csv, glob, json, os, sys, bisect
from collections import defaultdict

OUTP = sys.argv[1]
SPOT = "/data/tk0313/out"
FUT = "/data/tk0289/f6v/out"
FUTKEY = "p18a6vv2_10000"


def read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(l for l in f if not l.startswith("#")))


def costs(r):
    q = float(r["qty"]); px = float(r["entry_px"]); net = float(r["net_bps"])
    stop = r["reason"] in ("stop", "deadline")
    gross = net + 1.26 + (3.15 if stop else 1.26)
    k = q * px / 1e4
    return {"gross": gross, "usd": q * px, "a": net * k,
            "b": (gross - 20.0) * k,
            "v": (gross - 5.625 - (6.75 if stop else 5.625)) * k, "gross_usd": gross * k}


SIGF = ["size_share15_bp", "opp_wall_dist_cbps", "wall_add_max_age_ms", "vpin_bp", "since_far_ms", "obi5_bp"]
rows = []
for rd in sorted(glob.glob(f"{SPOT}/run-*")):
    day = "-".join(rd.rsplit("-", 3)[-3:])
    d = f"{rd}/out/{day}/s0"
    if not os.path.exists(f"{d}/rounds.csv"):
        continue
    sig = {(s["symbol"], s["signal_index"]): s for s in read(f"{d}/signals.csv")}
    for r in read(f"{d}/rounds.csv"):
        c = costs(r)
        s = sig.get((r["symbol"], r["signal_index"]), {})
        t0 = int(r["t0_ns"]); ex = int(r["exit_ns"]) if r["exit_ns"] else t0
        c.update(sym=r["symbol"], day=day, mon=day[:7], reason=r["reason"], hold=(ex - t0) / 6e10,
                 hour=(t0 // 3600_000_000_000) % 24, legs=r["legs_filled"], ff=float(r["fill_frac"]),
                 dir=r["dir"], sig_found=bool(s))
        for k in SIGF:
            try:
                c[k] = float(s[k])
            except (KeyError, ValueError):
                c[k] = None
        rows.append(c)
N = len(rows)
out = []
P = out.append
tot = {k: sum(r[k] for r in rows) for k in ("a", "b", "v", "gross_usd", "usd")}
P(f"## Итого S0\n\nn = {N}, дней {len({r['day'] for r in rows})}; Σ$ (а) {tot['a']:.0f} · (б) {tot['b']:.0f} · (в) {tot['v']:.0f}; "
  f"Σ gross$ до издержек {tot['gross_usd']:.0f}; оборот Σ qty·px {tot['usd']:.0f} $; "
  f"средний gross {sum(r['gross'] for r in rows)/N:.2f} bps; join с signals: {sum(r['sig_found'] for r in rows)}/{N}\n")


def table(title, keyf, order=None, base="v"):
    g = defaultdict(list)
    for r in rows:
        g[keyf(r)].append(r)
    ks = order or sorted(g, key=lambda k: sum(r[base] for r in g[k]))
    P(f"### {title}\n\n| группа | n | доля | gross bps (ср.) | Σ gross$ | Σ$ (а) | Σ$ (б) | Σ$ (в) | ср. (в) bps |\n|---|---|---|---|---|---|---|---|---|")
    for k in ks:
        x = g.get(k, [])
        if not x:
            continue
        u = sum(r["usd"] for r in x)
        P(f"| {k} | {len(x)} | {100*len(x)/N:.1f}% | {sum(r['gross'] for r in x)/len(x):.2f} | {sum(r['gross_usd'] for r in x):.0f} | "
          f"{sum(r['a'] for r in x):.0f} | {sum(r['b'] for r in x):.0f} | {sum(r['v'] for r in x):.0f} | {1e4*sum(r['v'] for r in x)/u:.2f} |")
    P("")


table("1. По причине выхода", lambda r: r["reason"])
HB = [(0, 5, "<5 мин"), (5, 30, "5–30 мин"), (30, 120, "30–120 мин"), (120, 240, "2–4 ч"), (240, 1e9, ">4 ч")]
hbin = lambda r: next(n for lo, hi, n in HB if lo <= r["hold"] < hi)
table("2. По времени от сигнала до выхода (включая ожидание заполнения лестницы)", hbin, [n for *_, n in HB])
table("3a. Заполнение лестницы: legs_filled", lambda r: r["legs"])
table("3b. fill_frac", lambda r: "1.0" if r["ff"] >= 0.999 else ("0.5–1" if r["ff"] >= 0.5 else "<0.5"))
P("Вход: цена входа против цены через 1/5/30 с после — в rounds/signals нет (есть только entry_px/exit_px/t0); для этого нужен новый проход по бинлогу. НЕ посчитано.\n")
table("4. Месяц", lambda r: r["mon"], sorted({r["mon"] for r in rows}))
# 5 топ-10 монет
g = defaultdict(list)
for r in rows:
    g[r["sym"]].append(r)
sl = sorted(g, key=lambda k: sum(r["v"] for r in g[k]))
P("### 5. Монеты: 10 худших по Σ$ (в)\n\n| монета | n | Σ$ (в) | % минуса | Σ$ (а) | gross bps |\n|---|---|---|---|---|---|")
for k in sl[:10]:
    x = g[k]
    P(f"| {k} | {len(x)} | {sum(r['v'] for r in x):.0f} | {100*sum(r['v'] for r in x)/tot['v']:.1f}% | {sum(r['a'] for r in x):.0f} | {sum(r['gross'] for r in x)/len(x):.2f} |")
P(f"\nМонет всего {len(g)}, с Σ$(в)<0: {sum(1 for k in g if sum(r['v'] for r in g[k])<0)}.\n")

# Парето
def qbins(key, nb=4):
    vs = sorted(r[key] for r in rows if r[key] is not None)
    cuts = [vs[int(len(vs) * i / nb)] for i in range(1, nb)]
    def f(r):
        if r[key] is None:
            return "нет"
        i = bisect.bisect_right(cuts, r[key])
        return f"Q{i+1}" + (f" (≤{cuts[i]:.0f})" if i < len(cuts) else f" (>{cuts[-1]:.0f})")
    return f

FEATS = [("причина выхода", lambda r: r["reason"]), ("удержание", hbin), ("монета", lambda r: r["sym"]),
         ("час суток UTC", lambda r: f"{r['hour']:02d}"), ("месяц", lambda r: r["mon"]),
         ("legs_filled", lambda r: r["legs"]), ("fill_frac", lambda r: "1.0" if r["ff"] >= 0.999 else ("0.5–1" if r["ff"] >= 0.5 else "<0.5")),
         ("доля стены в стакане size_share15_bp (квартили)", qbins("size_share15_bp")),
         ("расстояние до противоположной стены opp_wall_dist_cbps (квартили)", qbins("opp_wall_dist_cbps")),
         ("возраст всплеска wall_add_max_age_ms (квартили)", qbins("wall_add_max_age_ms")),
         ("vpin_bp (квартили)", qbins("vpin_bp")), ("since_far_ms (квартили)", qbins("since_far_ms")), ("obi5_bp (квартили)", qbins("obi5_bp"))]
P("## Парето 80 % минуса (на (в); минус = Σ$ всех групп, группы с Σ$<0 по возрастанию)\n")
P("Не измерены (нет в rounds/signals, нужен новый проход): режим, usd стены в точном значении (S0 = все ≥$10k), сила В-67, σ.\n")
P("| признак | групп всего | групп в наборе | набор | % минуса | % сделок | лифт | мес. с Σ$(набор)<0 |\n|---|---|---|---|---|---|---|---|")
months = sorted({r["mon"] for r in rows})
detail = {}
pareto = []
for name, f in FEATS:
    g = defaultdict(list)
    for r in rows:
        g[f(r)].append(r)
    sums = {k: sum(r["v"] for r in x) for k, x in g.items()}
    neg = sorted((k for k in g if sums[k] < 0), key=lambda k: sums[k])
    sel, cum = [], 0.0
    for k in neg:
        sel.append(k); cum += sums[k]
        if cum / tot["v"] >= 0.8:
            break
    reached = cum / tot["v"] >= 0.8
    nsel = sum(len(g[k]) for k in sel)
    mneg = 0
    mrows = []
    for m in months:
        mt = sum(r["v"] for r in rows if r["mon"] == m)
        ms = sum(r["v"] for k in sel for r in g[k] if r["mon"] == m)
        mn = sum(1 for k in sel for r in g[k] if r["mon"] == m)
        mrows.append((m, mn, ms, mt))
        mneg += ms < 0
    lift = (100 * cum / tot["v"]) / (100 * nsel / N)
    shown = ", ".join(map(str, sel[:6])) + (f" … (+{len(sel)-6})" if len(sel) > 6 else "")
    P(f"| {name} | {len(g)} | {len(sel)}{'' if reached else ' (80 % не достигнуто)'} | {shown} | {100*cum/tot['v']:.0f}% | {100*nsel/N:.0f}% | {lift:.2f} | {mneg}/{len(months)} |")
    pareto.append((name, len(g), len(sel), 100 * cum / tot["v"], 100 * nsel / N, lift, mrows, reached))
P("")
P("### Помесячная проверка наборов (Σ$ (в) набора / Σ$ месяца; n набора)\n")
for name, ng, ns, pc, pt, lift, mrows, reached in pareto:
    if name in ("месяц",):
        continue
    P(f"**{name}** — " + "; ".join(f"{m[5:]}: {ms:.0f}/{mt:.0f} (n {mn})" for m, mn, ms, mt in mrows))
    P("")

# фьюч-аналог
frows = []
common_days = {r["day"] for r in rows}
for rd in sorted(glob.glob(f"{FUT}/run-*")):
    day = "-".join(rd.rsplit("-", 3)[-3:])
    p = f"{rd}/out/{day}/{FUTKEY}-{day[:7]}/rounds.csv"
    if day in common_days and os.path.exists(p):
        for r in read(p):
            c = costs(r); c.update(sym=r["symbol"], reason=r["reason"], day=day, mon=day[:7],
                                   hold=((int(r["exit_ns"]) if r["exit_ns"] else int(r["t0_ns"])) - int(r["t0_ns"])) / 6e10)
            frows.append(c)
ssyms = {r["sym"] for r in rows}
fc = [r for r in frows if r["sym"] in ssyms]
P(f"## Фьюч-аналог {FUTKEY} (≠ S0: поток ≥ 2 %), общие сутки и монеты спота\n")
P(f"n = {len(fc)}, Σ$ (а) {sum(r['a'] for r in fc):.0f}, Σ gross$ {sum(r['gross_usd'] for r in fc):.0f}, ср. gross {sum(r['gross'] for r in fc)/max(1,len(fc)):.2f} bps\n")
P("| причина | n | доля | gross bps | Σ$ (а) |\n|---|---|---|---|---|")
for k in sorted({r["reason"] for r in fc}):
    x = [r for r in fc if r["reason"] == k]
    P(f"| {k} | {len(x)} | {100*len(x)/len(fc):.1f}% | {sum(r['gross'] for r in x)/len(x):.2f} | {sum(r['a'] for r in x):.0f} |")
P("\n| удержание | n | Σ$ (а) | gross bps |\n|---|---|---|---|")
for lo, hi, n in HB:
    x = [r for r in fc if lo <= r["hold"] < hi]
    if x:
        P(f"| {n} | {len(x)} | {sum(r['a'] for r in x):.0f} | {sum(r['gross'] for r in x)/len(x):.2f} |")
open(OUTP + ".md", "w").write("\n".join(out))
print("ok", N, len(fc))
