"""График пары Bybit–OKX по одной монете: Sell/Buy на уровне событий (всё окно) и приближения к лучшим эпизодам.

Запуск: python3 skhynix_plot.py <series.npz> <res.json> <res.json.eps> <выход.png>
Вход — выход skhynix_look.py. Время на осях — GMT+4. Sell и Buy — % (bps / 100), без укрупнения по слотам.
"""
import json
import sys

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


def style(ax):
    ax.set_facecolor(SURF)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9)
    ax.grid(True, color=GRID, lw=0.6)
    ax.set_axisbelow(True)


def main(npz, res_json, eps_json, out):
    d = np.load(npz)
    res = json.load(open(res_json, encoding="utf-8"))
    eps = json.load(open(eps_json, encoding="utf-8"))
    t0, t1 = res["t0"], res["t1"]
    T = d["T"]
    w = (T >= t0) & (T < t1)
    Tw = T[w]
    S, Bu, c = d["S"][w] / 100.0, d["Bu"][w] / 100.0, d["c"][w] / 100.0
    F = res["fees_bps"]["taker4"] / 100.0
    X = dt64(Tw)
    plt.rcParams.update({"font.family": "DejaVu Sans", "path.simplify": False, "agg.path.chunksize": 50000})
    fig = plt.figure(figsize=(17, 17), dpi=110, facecolor=SURF)
    gs = fig.add_gridspec(4, 2, height_ratios=[3.2, 2.2, 2.4, 2.4], hspace=0.42, wspace=0.16, left=0.065, right=0.968, top=0.925, bottom=0.04)
    fig.suptitle("SKHYNIX: Bybit SKHYNIXUSDT − OKX SKHYNIX-USDT-SWAP, исполнимые Sell и Buy по событиям книг (мс)", fontsize=14, color=INK, x=0.065, ha="left", y=0.975)
    fig.text(0.065, 0.952, f"{label(t0)[:-4]} → {label(t1)[:-4]} GMT+4 · Sell = bid Bybit / ask OKX − 1, Buy = ask Bybit / bid OKX − 1 · линия «центр» — 60-мин медиана середины (как на вкладке)",
             fontsize=9.5, color=INK2)
    wk0 = 1794614400000   # 14.11 00:00 UTC — выходные KRX
    close = 1794551400000  # 13.11 06:30 UTC — закрытие KRX
    for r, (ylim, ttl) in enumerate(((None, "Всё окно, ось по вертикали — весь размах (одиночные выбросы видны)"), ((-0.08, 0.08), "То же окно, ось по вертикали ±0,08 % (основная масса событий)"))):
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
        ax.axvspan(dt64(wk0), dt64(t1), color="#efeeea", zorder=0)
        ax.axvline(dt64(close), color=INK2, lw=0.8)
        if r == 0:
            ax.text(dt64(close), ax.get_ylim()[1], " закрытие KRX пт 06:30 UTC", va="top", ha="left", fontsize=8.5, color=INK2)
            ax.text(dt64(wk0), ax.get_ylim()[1], " выходные (сб–вс UTC)", va="top", ha="left", fontsize=8.5, color=INK2)
        ax.set_ylabel("%", color=INK2)
        ax.set_title(ttl, loc="left", fontsize=10.5, color=INK)
        ax.set_xlim(dt64(t0), dt64(t1))
        ax.xaxis.set_major_locator(mdates.HourLocator(byhour=range(0, 24, 6)))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M"))
        if r == 0:
            ax.legend(loc="lower left", fontsize=8.5, frameon=False, ncol=2)
    # приближения: четыре эпизода порога тейкерских комиссий (вкладочный счёт)
    rows = sorted(eps["taker4"], key=lambda r: r["t"])
    for k, ep in enumerate(rows[:4]):
        ax = fig.add_subplot(gs[2 + k // 2, k % 2])
        style(ax)
        t = ep["t"]
        a, b = t - 250, t + max(750, ep["tau_ms"] + 250)
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
        if not ep["cens"]:
            ax.axvline(ep["tau_ms"], color=INK2, lw=0.8, ls=":")
        i0 = int(np.searchsorted(T, t))
        ep_px = (d["S"][i0] if ep["dir"] == 1 else d["Bu"][i0]) / 100.0
        ax.plot([0], [ep_px], marker="v" if ep["dir"] == 1 else "^", color=INK, ms=8, zorder=5)
        side = "Sell" if ep["dir"] == 1 else "Buy"
        net = (ep["gross"] - res["fees_bps"]["taker4"]) / 100.0
        ax.set_title(f"{label(t)} GMT+4 · вход по {side}", loc="left", fontsize=10, color=INK)
        ax.text(0.99, 0.03, f"в {ep['e0'] / 100:.3f} % от центра · глубина входа {DOL}{ep['din']:.0f}, выхода {DOL}{ep['dout']:.0f}\nсхождение {ep['tau_ms']} мс · валовая {ep['gross'] / 100:.3f} %, после 4 ног {net:+.3f} %",
                transform=ax.transAxes, ha="right", va="bottom", fontsize=8.5, color=INK, bbox={"fc": SURF, "ec": GRID, "pad": 3})
        ax.set_xlim(-250, b - t)
        ax.set_xlabel("мс от входа (треугольник — цена входа, пунктир — схождение)", color=INK2, fontsize=9)
        ax.set_ylabel("%", color=INK2)
        if k == 0:
            ax.legend(loc="upper right", fontsize=8, frameon=False)
    fig.savefig(out, dpi=110, facecolor=SURF)
    print("записано", out)


if __name__ == "__main__":
    main(*sys.argv[1:5])
