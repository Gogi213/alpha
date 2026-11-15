"""График пары Bybit–OKX по одной монете: Sell/Buy на уровне событий (всё окно) и приближения к лучшим эпизодам.

Запуск: python3 pair_plot.py <series.npz> <res.json> <res.json.eps> <выход.png> --title "<заголовок>" [--clip <±%>] [--krx] [--burst-from <мс>]
Вход — выход pair_look.py. Время на осях — GMT+4. Sell и Buy — % (bps / 100), без укрупнения по слотам.
--krx: отметки закрытия KRX и выходных (SK hynix). --burst-from: добавляет панель «последние часы» с отметками эпизодов (I) порога 0,1260 %
и приближения: 3 лучших по чистой прибыли за всё окно + 3 лучших с этого момента (без --burst-from — первые четыре эпизода по времени).
"""
import argparse
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

SURF, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e3e2de"
C_SELL, C_BUY, C_CEN = "#eb6834", "#2a78d6", "#7a7974"
TZ = 4 * 3_600_000
DOW = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
DOL = "\\$"   # «$» в тексте matplotlib без разметки формул


def dt64(ms):
    return (np.asarray(ms, np.int64) + TZ).astype("datetime64[ms]")


def label(ms):
    t = dt64(ms).astype(object)
    return f"{DOW[t.weekday()]} {t:%d.%m %H:%M:%S}.{int(ms) % 1000:03d}"


def dd(x):
    return f"{x:.1f}" if x < 10 else f"{x:.0f}"


def style(ax):
    ax.set_facecolor(SURF)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9)
    ax.grid(True, color=GRID, lw=0.6)
    ax.set_axisbelow(True)


def zoom(ax, d, T, ep, F, F_bps, first):
    style(ax)
    t = ep["t"]
    tau = ep["tau_ms"]
    a, b = t - 250, t + int(min(max(750, tau + 250), 5000))
    m = (T >= a) & (T <= b)
    j0 = max(int(np.searchsorted(T, a, "right")) - 1, 0)   # состояние на начало окна
    m[j0] = True
    x = (T[m] - t).astype(float)
    x[0] = max(x[0], a - t)
    s_, b_, c_ = d["S"][m] / 100.0, d["Bu"][m] / 100.0, d["c"][m] / 100.0
    xe = np.r_[x, b - t]
    ax.step(xe, np.r_[s_, s_[-1]], where="post", color=C_SELL, lw=1.3, label="Sell")
    ax.step(xe, np.r_[b_, b_[-1]], where="post", color=C_BUY, lw=1.3, label="Buy")
    ax.plot(x, s_, "o", color=C_SELL, ms=3.2)
    ax.plot(x, b_, "o", color=C_BUY, ms=3.2)
    ax.step(xe, np.r_[c_, c_[-1]], where="post", color=C_CEN, lw=1.0, label="центр")
    thr = (c_ + F) if ep["dir"] == 1 else (c_ - F)
    ax.step(xe, np.r_[thr, thr[-1]], where="post", color=INK2, lw=0.9, ls=(0, (4, 3)), label="порог входа: центр ± 0,126 %")
    ax.axvline(0, color=INK, lw=0.8)
    if not ep["cens"] and tau <= b - t:
        ax.axvline(tau, color=INK2, lw=0.8, ls=":")
    i0 = int(np.searchsorted(T, t))
    ax.plot([0], [(d["S"][i0] if ep["dir"] == 1 else d["Bu"][i0]) / 100.0], marker="v" if ep["dir"] == 1 else "^", color=INK, ms=8, zorder=5)
    side = "Sell" if ep["dir"] == 1 else "Buy"
    net = (ep["gross"] - F_bps) / 100.0
    ax.set_title(f"{label(t)} GMT+4 · вход по {side}", loc="left", fontsize=10, color=INK)
    ax.text(0.99, 0.03, f"в {ep['e0'] / 100:.3f} % от центра · глубина входа {DOL}{dd(ep['din'])}, выхода {DOL}{dd(ep['dout'])}\nсхождение {tau} мс · валовая {ep['gross'] / 100:.3f} %, после 4 ног {net:+.3f} %",
            transform=ax.transAxes, ha="right", va="bottom", fontsize=8.5, color=INK, bbox={"fc": SURF, "ec": GRID, "pad": 3})
    ax.set_xlim(-250, b - t)
    ax.set_xlabel("мс от входа (треугольник — цена входа, пунктир — схождение)", color=INK2, fontsize=9)
    ax.set_ylabel("%", color=INK2)
    if first:
        ax.legend(loc="upper right", fontsize=8, frameon=False)


def main(a):
    d = np.load(a.npz)
    res = json.load(open(a.res, encoding="utf-8"))
    eps = json.load(open(a.eps, encoding="utf-8"))
    t0, t1 = res["t0"], res["t1"]
    T = d["T"]
    w = (T >= t0) & (T < t1)
    Tw = T[w]
    S, Bu, c = d["S"][w] / 100.0, d["Bu"][w] / 100.0, d["c"][w] / 100.0
    F_bps = res["fees_bps"]["taker4"]
    F = F_bps / 100.0
    X = dt64(Tw)
    rows = [r for r in sorted(eps["taker4"], key=lambda r: r["t"]) if r["t"] >= t0 and r["gross"] is not None]
    if a.burst_from:
        by_net = sorted(rows, key=lambda r: -(r["gross"]))
        best = []
        for r in by_net:
            if len(best) < 3 and all(abs(r["t"] - q["t"]) > 2000 for q in best):
                best.append(r)
        in_b = []
        for r in by_net:
            if r["t"] >= a.burst_from and len(in_b) < 3 and all(abs(r["t"] - q["t"]) > 2000 for q in best + in_b):
                in_b.append(r)
        zooms, ncol, nz_rows = sorted(best, key=lambda r: r["t"]) + sorted(in_b, key=lambda r: r["t"]), 3, 2
    else:
        zooms, ncol, nz_rows = rows[:4], 2, 2
    n_top = 3 if a.burst_from else 2
    plt.rcParams.update({"font.family": "DejaVu Sans", "path.simplify": False, "agg.path.chunksize": 50000})
    fig = plt.figure(figsize=(17, 11 + 3.2 * (1 if a.burst_from else 0) + 3 * nz_rows), dpi=110, facecolor=SURF)
    ratios = [3.2, 2.2] + ([2.4] if a.burst_from else []) + [2.4] * nz_rows
    gs = fig.add_gridspec(len(ratios), ncol, height_ratios=ratios, hspace=0.5, wspace=0.22, left=0.065, right=0.968, top=0.94, bottom=0.04)
    fig.suptitle(a.title, fontsize=14, color=INK, x=0.065, ha="left", y=0.985)
    fig.text(0.065, 0.967, f"{label(t0)[:-4]} → {label(t1)[:-4]} GMT+4 · Sell = bid Bybit / ask OKX − 1, Buy = ask Bybit / bid OKX − 1 · линия «центр» — 60-мин медиана середины (как на вкладке)",
             fontsize=9.5, color=INK2)
    wk0, close = 1794614400000, 1794551400000   # 14.11 00:00 UTC (выходные KRX), 13.11 06:30 UTC (закрытие KRX)
    panels = [(None, "Всё окно, ось по вертикали — весь размах (одиночные выбросы видны)"), ((-a.clip, a.clip), f"То же окно, ось по вертикали ±{str(a.clip).replace('.', ',')} % (основная масса событий)")]
    for r, (ylim, ttl) in enumerate(panels):
        ax = fig.add_subplot(gs[r, :])
        style(ax)
        ax.plot(X, S, color=C_SELL, lw=0.45, label="Sell (продать Bybit по биду / купить OKX по оферу)")
        ax.plot(X, Bu, color=C_BUY, lw=0.45, label="Buy (купить Bybit по оферу / продать OKX по биду)")
        ax.plot(X, c, color=C_CEN, lw=1.0, label="центр (60 мин)")
        if ylim is None:
            ax.plot(X, c + F, color=INK2, lw=0.9, ls=(0, (4, 3)), label="центр ± 0,126 % (4 тейкерские ноги)")
            ax.plot(X, c - F, color=INK2, lw=0.9, ls=(0, (4, 3)))
        else:
            ax.set_ylim(*ylim)
        if a.krx:
            ax.axvspan(dt64(wk0), dt64(t1), color="#efeeea", zorder=0)
            ax.axvline(dt64(close), color=INK2, lw=0.8)
            if r == 0:
                ax.text(dt64(close), ax.get_ylim()[1], " закрытие KRX пт 06:30 UTC", va="top", ha="left", fontsize=8.5, color=INK2)
                ax.text(dt64(wk0), ax.get_ylim()[1], " выходные (сб–вс UTC)", va="top", ha="left", fontsize=8.5, color=INK2)
        if a.burst_from:
            ax.axvspan(dt64(a.burst_from), dt64(t1), color="#efeeea", zorder=0)
            if r == 0:
                ax.text(dt64(a.burst_from), ax.get_ylim()[1], "последние часы (панель ниже) ", va="top", ha="right", fontsize=8.5, color=INK2)
        ax.set_ylabel("%", color=INK2)
        ax.set_title(ttl, loc="left", fontsize=10.5, color=INK)
        ax.set_xlim(dt64(t0), dt64(t1))
        ax.xaxis.set_major_locator(mdates.HourLocator(byhour=range(0, 24, 6)))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M"))
        if r == 0:
            ax.legend(loc="lower left", fontsize=8.5, frameon=False, ncol=2)
    if a.burst_from:
        ax = fig.add_subplot(gs[2, :])
        style(ax)
        lo = a.burst_from - 3_600_000
        m = Tw >= lo
        ax.plot(X[m], S[m], color=C_SELL, lw=0.6, label="Sell")
        ax.plot(X[m], Bu[m], color=C_BUY, lw=0.6, label="Buy")
        ax.plot(X[m], c[m], color=C_CEN, lw=1.0, label="центр (60 мин)")
        ax.plot(X[m], (c + F)[m], color=INK2, lw=0.9, ls=(0, (4, 3)), label="центр ± 0,126 %")
        ax.plot(X[m], (c - F)[m], color=INK2, lw=0.9, ls=(0, (4, 3)))
        ev = [r for r in rows if r["t"] >= lo]
        ylo = ax.get_ylim()[0]
        ax.plot(dt64([r["t"] for r in ev]), [ylo] * len(ev), "|", color=INK, ms=12, mew=1.4, label=f"эпизоды (I) порога 0,1260 %: {len(ev)}")
        ax.axvline(dt64(a.burst_from), color=INK2, lw=0.8)
        ax.set_title("Последние часы (с запасом в час до серого поля): событие за событием; чёрные штрихи внизу — входы эпизодов порога 0,1260 %", loc="left", fontsize=10.5, color=INK)
        ax.set_ylabel("%", color=INK2)
        ax.set_xlim(dt64(lo), dt64(t1))
        ax.xaxis.set_major_locator(mdates.HourLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M"))
        ax.legend(loc="upper right", fontsize=8.5, frameon=False, ncol=2)
    z0 = len(ratios) - nz_rows
    for k, ep in enumerate(zooms[: ncol * nz_rows]):
        zoom(fig.add_subplot(gs[z0 + k // ncol, k % ncol]), d, T, ep, F, F_bps, k == 0)
    fig.savefig(a.out, dpi=110, facecolor=SURF)
    print("записано", a.out)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("npz")
    p.add_argument("res")
    p.add_argument("eps")
    p.add_argument("out")
    p.add_argument("--title", required=True)
    p.add_argument("--clip", type=float, default=0.08)
    p.add_argument("--krx", action="store_true")
    p.add_argument("--burst-from", type=int, default=0)
    main(p.parse_args())
