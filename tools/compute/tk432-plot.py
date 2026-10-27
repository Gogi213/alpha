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
    fig = plt.figure(figsize=(12, 20), dpi=100)
    outer = fig.add_gridspec(6, 2, hspace=0.45, wspace=0.18, left=0.05, right=0.99, top=0.945, bottom=0.025)
    fig.suptitle("TK-432 · лист %d · #%d–%d · время GMT+4 · зелёная=старт, красная=пик, синяя=конец" % (sheet + 1, sheet * 12 + 1, sheet * 12 + len(items)), fontsize=13, y=0.992)
    for k, r in enumerate(items):
        inner = outer[k // 2, k % 2].subgridspec(2, 1, height_ratios=[3, 1], hspace=0.05)
        ax = fig.add_subplot(inner[0]); bx = fig.add_subplot(inner[1], sharex=ax)
        rows = D[r["id"]]
        x = [dt(t) for t, *_ in rows]
        ax.fill_between(x, [v[3] for v in rows], [v[2] for v in rows], color="#9bb7d4", lw=0)
        ax.plot(x, [v[4] for v in rows], color="#1f3b57", lw=1.0)
        if len(rows) < 400:
            ax.plot(x, [v[4] for v in rows], ".", color="#1f3b57", ms=3)
        bx.bar(x, [v[5] for v in rows], width=1 / 1440, color="#7a7a7a", lw=0)
        for key, c in (("t_start_ms", "#1a9a1a"), ("t_peak_ms", "#d62020"), ("t_end_ms", "#1f5fd6")):
            for a in (ax, bx):
                a.axvline(dt(int(r[key])), color=c, lw=1.4)
        ax.axvspan(dt(int(r["t_start_ms"])), dt(int(r["t_end_ms"])), color="#ffe9a8", alpha=0.45, lw=0)
        ax.set_title("#%s  %s  %s  %s  ход %s%%  %s мин  (%s)" % (r["n"], r["id"], r["монета"], MK[(r["биржа"], r["рынок"])], r["ход_%"], r["длит_мин"], "вверх" if r["знак"] == "+" else "вниз"), fontsize=10, loc="left")
        ax.tick_params(labelbottom=False, labelsize=8); bx.tick_params(labelsize=8)
        bx.xaxis.set_major_formatter(md.DateFormatter("%d.%m %H:%M", tz=TZ))
        bx.xaxis.set_major_locator(md.AutoDateLocator(minticks=3, maxticks=4, tz=TZ))
        ax.grid(alpha=0.3); bx.grid(alpha=0.3)
        bx.yaxis.set_major_formatter(FuncFormatter(lambda v, _: "%gM" % (v / 1e6) if v >= 1e6 else "%gk" % (v / 1e3) if v >= 1e3 else "%g" % v))
        bx.set_ylabel("оборот $", fontsize=7)
    fig.savefig("%s/pumps-sheet%d.png" % (out, sheet + 1))
    plt.close(fig)
