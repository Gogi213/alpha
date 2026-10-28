"""TK-433: по PNG на событие (цена 1 м + оборот), вертикали старт/конец(пик). Врезка ±2 ч, если длит. < 3 ч.
Использование: tk433-plot.py pick.csv cand.tsv <каталог выхода>"""
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
fmt = md.DateFormatter("%d.%m %H:%M", tz=TZ)
qf = FuncFormatter(lambda v, _: "%gM" % (v / 1e6) if v >= 1e6 else "%gk" % (v / 1e3) if v >= 1e3 else "%g" % v)
for r in pick:
    rows = D[r["id"]]
    full = rows
    if len(rows) > 3000:  # длинные окна: корзины по max(high)/min(low)/last(close)/sum(q)
        k = -(-len(rows) // 1500)
        rows = [(g[0][0], g[0][1], max(v[2] for v in g), min(v[3] for v in g), g[-1][4], sum(v[5] for v in g)) for g in (rows[i:i + k] for i in range(0, len(rows), k))]
    ts, te = int(r["t_start_ms"]), int(r["t_end_ms"])
    short = (te - ts) < 3 * 3600000
    fig = plt.figure(figsize=(12, 7 if short else 5), dpi=100)
    gs = fig.add_gridspec(3 if short else 2, 1, height_ratios=[2, 0.8, 2.4] if short else [3, 1], hspace=0.4 if short else 0.05, left=0.07, right=0.99, top=0.92, bottom=0.07)
    ax = fig.add_subplot(gs[0]); bx = fig.add_subplot(gs[1], sharex=ax)
    x = [dt(v[0]) for v in rows]
    ax.vlines(x, [v[3] for v in rows], [v[2] for v in rows], color="#5b8bbd", lw=1.0)
    ax.plot(x, [v[4] for v in rows], color="#1f3b57", lw=0.8)
    bx.bar(x, [v[5] for v in rows], width=(rows[1][0] - rows[0][0]) / 86400000 if len(rows) > 1 else 1 / 1440, color="#7a7a7a", lw=0)
    for a_ in (ax, bx):
        a_.axvline(dt(ts), color="#1a9a1a", lw=1.2); a_.axvline(dt(te), color="#d62020", lw=1.2); a_.grid(alpha=0.3)
    ax.axvspan(dt(ts), dt(te), color="#ffe9a8", alpha=0.45, lw=0)
    fig.suptitle("#%s  %s  %s  %s  T=%s  %s мин (%s) · GMT+4 · зелёная=старт, красная=конец(пик)" % (r["n"], r["id"], r["монета"], MK[(r["биржа"], r["рынок"])], r["id"].split("-")[0][1:], r["длит_мин"], "вверх" if r["знак"] == "+" else "вниз"), fontsize=10, x=0.07, ha="left")
    ax.tick_params(labelbottom=False, labelsize=8); bx.tick_params(labelsize=8)
    bx.yaxis.set_major_formatter(qf); bx.set_ylabel("оборот $", fontsize=7)
    bx.xaxis.set_major_formatter(fmt); bx.xaxis.set_major_locator(md.AutoDateLocator(minticks=3, maxticks=6, tz=TZ))
    if short:
        bx.tick_params(labelbottom=False)
        cx = fig.add_subplot(gs[2])
        w0, w1 = ts - 7200000, te + 7200000
        sub = [v for v in full if w0 <= v[0] <= w1]
        xs = [dt(v[0]) for v in sub]
        cx.vlines(xs, [v[3] for v in sub], [v[2] for v in sub], color="#5b8bbd", lw=1.6)
        cx.plot(xs, [v[4] for v in sub], color="#1f3b57", lw=0.9)
        cx.axvline(dt(ts), color="#1a9a1a", lw=1.2); cx.axvline(dt(te), color="#d62020", lw=1.2)
        cx.axvspan(dt(ts), dt(te), color="#ffe9a8", alpha=0.45, lw=0)
        cx.set_xlim(dt(w0), dt(w1)); cx.grid(alpha=0.3); cx.tick_params(labelsize=8)
        cx.set_title("врезка ±2 ч", fontsize=8, loc="left", pad=2)
        cx.xaxis.set_major_formatter(fmt); cx.xaxis.set_major_locator(md.AutoDateLocator(minticks=3, maxticks=6, tz=TZ))
    fig.savefig("%s/%02d.png" % (out, int(r["n"])))
    plt.close(fig)
