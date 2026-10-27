"""TK-432: два листа PNG по 12 графиков (цена 1 м + оборот), вертикали старт/пик/конец.
Использование: tk432-plot.py pick.csv cand.tsv <каталог выхода>"""
import csv, os, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as md
from matplotlib.ticker import FuncFormatter

TZ = timezone(timedelta(hours=4))
pick = list(csv.DictReader(open(sys.argv[1], encoding="utf-8")))
D = defaultdict(list)
for r in csv.DictReader(open(sys.argv[2], encoding="utf-8"), delimiter="\t"):
    D[r["id"]].append((int(r["t_ms"]), float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"]), float(r["q"])))
out = sys.argv[3]
os.makedirs(out, exist_ok=True)
dt = lambda ms: datetime.fromtimestamp(ms / 1000, TZ)
MK = {("bybit", "linear"): "Bybit linear", ("bybit", "spot"): "Bybit spot", ("gate", "futures_usdt"): "Gate fut", ("gate", "spot"): "Gate spot"}
for sheet in (0, 1):
    items = pick[sheet * 12:(sheet + 1) * 12]
    fig = plt.figure(figsize=(12, 24), dpi=100)
    outer = fig.add_gridspec(6, 2, hspace=0.5, wspace=0.18, left=0.05, right=0.99, top=0.955, bottom=0.02)
    fig.suptitle("TK-432 · лист %d · #%d–%d · время GMT+4 · зелёная=старт, красная=пик, синяя=конец · полоса = low–high минуты" % (sheet + 1, sheet * 12 + 1, sheet * 12 + len(items)), fontsize=12, y=0.994)
    for k, r in enumerate(items):
        rows = D[r["id"]]
        ts, tp, te = (int(r[key]) for key in ("t_start_ms", "t_peak_ms", "t_end_ms"))
        short = (te - ts) < 3600000
        inner = outer[k // 2, k % 2].subgridspec(3 if short else 2, 1, height_ratios=[2, 0.8, 2.4] if short else [3, 1], hspace=0.38 if short else 0.05)
        ax = fig.add_subplot(inner[0]); bx = fig.add_subplot(inner[1], sharex=ax)
        x = [dt(t) for t, *_ in rows]
        lo = [v[3] for v in rows]; hi = [v[2] for v in rows]; cl = [v[4] for v in rows]
        ax.vlines(x, lo, hi, color="#5b8bbd", lw=1.0)
        ax.plot(x, cl, color="#1f3b57", lw=0.8)
        bx.bar(x, [v[5] for v in rows], width=1 / 1440, color="#7a7a7a", lw=0)
        vw = 0.8 if short else 1.4
        for key, c in (("t_start_ms", "#1a9a1a"), ("t_peak_ms", "#d62020"), ("t_end_ms", "#1f5fd6")):
            for a_ in (ax, bx):
                a_.axvline(dt(int(r[key])), color=c, lw=vw, alpha=0.85)
        ax.axvspan(dt(ts), dt(te), color="#ffe9a8", alpha=0.45, lw=0)
        ax.set_title("#%s  %s  %s  %s  ход %s%%  %s мин  (%s)" % (r["n"], r["id"], r["монета"], MK[(r["биржа"], r["рынок"])], r["ход_%"], r["длит_мин"], "вверх" if r["знак"] == "+" else "вниз"), fontsize=10, loc="left")
        ax.tick_params(labelbottom=False, labelsize=8); bx.tick_params(labelsize=8)
        ax.grid(alpha=0.3); bx.grid(alpha=0.3)
        bx.yaxis.set_major_formatter(FuncFormatter(lambda v, _: "%gM" % (v / 1e6) if v >= 1e6 else "%gk" % (v / 1e3) if v >= 1e3 else "%g" % v))
        bx.set_ylabel("оборот $", fontsize=7)
        fmt = md.DateFormatter("%d.%m %H:%M", tz=TZ)
        bx.xaxis.set_major_formatter(fmt)
        bx.xaxis.set_major_locator(md.AutoDateLocator(minticks=3, maxticks=4, tz=TZ))
        if short:
            bx.tick_params(labelbottom=False)
            cx = fig.add_subplot(inner[2])
            w0, w1 = ts - 7200000, te + 7200000
            sub = [v for v in rows if w0 <= v[0] <= w1]
            xs = [dt(v[0]) for v in sub]
            cx.vlines(xs, [v[3] for v in sub], [v[2] for v in sub], color="#5b8bbd", lw=1.6)
            cx.plot(xs, [v[4] for v in sub], color="#1f3b57", lw=0.9)
            for key, c in (("t_start_ms", "#1a9a1a"), ("t_peak_ms", "#d62020"), ("t_end_ms", "#1f5fd6")):
                cx.axvline(dt(int(r[key])), color=c, lw=1.2, alpha=0.85)
            cx.axvspan(dt(ts), dt(te), color="#ffe9a8", alpha=0.45, lw=0)
            cx.set_xlim(dt(w0), dt(w1))
            cx.set_title("врезка ±2 ч (полоса low–high по минутам)", fontsize=8, loc="left", pad=2)
            cx.tick_params(labelsize=8); cx.grid(alpha=0.3)
            cx.xaxis.set_major_formatter(fmt)
            cx.xaxis.set_major_locator(md.AutoDateLocator(minticks=3, maxticks=5, tz=TZ))
    fig.savefig("%s/pumps-sheet%d.png" % (out, sheet + 1))
    plt.close(fig)
