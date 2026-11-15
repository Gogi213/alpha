"""Отчёт «только выбросы» по outlier.pkl (outlier_run.py): итоги по настройкам, рейтинг пар, топ-N. Всё в % (1 bps = 0,01 %) и в $.

Запуск: python3 outlier_report.py <outlier.pkl> [N=30] > отчёт.md  (таблицы markdown; текст вокруг — руками)
Рейтинг: группа «ok» (tt: ни одна нога не BingX; mk: хедж не на BingX), кругов ≥ окно_ч / 6 (≥ 1 входа за 6 ч в среднем по окну),
в каждых сутках окна (GMT+4) есть круги и медиана чистого итога круга > 0. Плацебо: то же с обратным знаком (в каждых сутках медиана < 0) — сколько пар
«проходят» рейтинг по шуму, если бы знак был случайным.
"""
import pickle
import sys
from collections import defaultdict

import numpy as np

H_LO = -400


def pc(x, d=3):
    return "—" if x is None else f"{x / 100:.{d}f}"


def usd(x):
    return "—" if x is None else f"{x:.0f}" if abs(x) >= 100 else f"{x:.1f}"


def hq(h, q, lo):
    """Квантиль по гистограмме (шаг 1 bps, левый край lo) -> bps (середина корзины)."""
    c = np.cumsum(h)
    if not c[-1]:
        return None
    return lo + float(np.searchsorted(c, q * c[-1])) + 0.5


def grp(k):
    m, _, x, y, _ = k
    return "bingx" if (("bingx" in (x, y)) if m == "tt" else y == "bingx") else "ok"


def rated(v, win_h):
    m = v["net_med_d"]
    return v["n"] >= win_h / 6 and all(x is not None and x > 0 for x in m)


def placebo(v, win_h):
    m = v["net_med_d"]
    return v["n"] >= win_h / 6 and all(x is not None and x < 0 for x in m)


def cfg_name(mode, cfg):
    th, lat, v = cfg
    if mode == "tt":
        return f"{th / 100:g} %", lat, "полное" if v == "full" else "половина"
    return f"{th / 100:g} %", lat, "тейкер" if v == "tt" else "мейкер"


def main(path):
    d = pickle.load(open(path, "rb"))
    R, Hs = d["rows"], d["hist"]
    win_h = (d["t1"] - d["t0"]) / 3.6e6
    by = defaultdict(lambda: defaultdict(float))
    for k, v in R.items():
        g, mode, cfg = grp(k), k[0], k[-1]
        a = by[(mode, cfg, g)]
        a["pairs"] += 1
        a["n"] += v["n"]
        a["closed"] += v["closed"]
        a["unclosed"] += v["unclosed"]
        a["usd_cap"] += v["usd_cap"]
        a["usd_full"] += v["usd_full"]
        a["active"] += v["n"] >= win_h / 6
        if rated(v, win_h):
            a["rated"] += 1
            a["rated_usd"] += v["usd_cap"]
            a["rated_trim"] += v["usd_cap_trim"]
        a["plac"] += placebo(v, win_h)
    print(f"Окно {win_h:.2f} ч; пар по режимам: " + ", ".join(f"{m}: {len({k[:4] for k in R if k[0] == m})}" for m in ("tt", "mk")) + "\n")
    for mode, title in (("tt", "Тейкер-тейкер"), ("mk", "Мейкер + хедж")):
        for g, gt in (("ok", "в рейтинге (без BingX)"), ("bingx", "BingX (вне рейтинга)")):
            print(f"### {title} — {gt}\n")
            print("| порог θ | задержка L, мс | " + ("закрытие" if mode == "tt" else "закрытие хеджа") + " | пар с кругами | кругов | не закрыто | "
                  "чистый итог круга, медиана / p25, % | кругов с итогом > 0 | расхождение на входе, медиана, % | валовый итог, медиана, % | "
                  "$/сут, сумма (≤ 100 $ на круг) | $/сут, сумма (полный объём) | пар с ≥ 1 входом за 6 ч | прошли рейтинг | $/сут прошедших | плацебо |")
            print("|" + "---|" * 16)
            for cfg in sorted({k[-1] for k in R if k[0] == mode}, key=lambda c: (c[1], c[2], c[0])):
                a = by.get((mode, cfg, g))
                if not a:
                    continue
                hn, hg, hd = Hs[(mode, cfg, g)]
                th, lat, v = cfg_name(mode, cfg)
                tot = hn.sum()
                pos = hn[-H_LO + 1:].sum() / tot if tot else None
                print(f"| {th} | {lat} | {v} | {a['pairs']:.0f} | {a['n']:.0f} | {a['unclosed']:.0f} | {pc(hq(hn, .5, H_LO))} / {pc(hq(hn, .25, H_LO))} | "
                      f"{'—' if pos is None else f'{pos * 100:.0f} %'} | {pc(hq(hd, .5, 0))} | {pc(hq(hg, .5, H_LO))} | {usd(a['usd_cap'])} | {usd(a['usd_full'])} | "
                      f"{a['active']:.0f} | {a['rated']:.0f} | {usd(a['rated_usd'])} | {a['plac']:.0f} |")
            print()
    return d, R, win_h


def top(R, win_h, mode, cfg, n=30, only_rated=True, g="ok", key="usd_cap"):
    rows = [(k, v) for k, v in R.items() if k[0] == mode and k[-1] == cfg and grp(k) == g and (not only_rated or rated(v, win_h))]
    rows.sort(key=lambda kv: -kv[1][key])
    return rows[:n], len(rows)


def print_top(R, win_h, mode, cfg, n, only_rated, title, g="ok"):
    rows, tot = top(R, win_h, mode, cfg, n, only_rated, g)
    print(f"### {title}\n")
    print(f"Пар в списке: {tot}; показаны первые {len(rows)}; сумма показанных = {usd(sum(v['usd_cap'] for _, v in rows))} $/сут (≤ 100 $ на круг), "
          f"без лучшего круга каждой пары = {usd(sum(v['usd_cap_trim'] for _, v in rows))}.\n")
    print("| # | монета | пара | входов 1ч/4ч/6ч/1сут | расхождение на входе, медиана, % | валовый итог, медиана, % | чистый итог, медиана / p25, % | кругов с итогом > 0 | "
          "размер на входе, медиана, $ | удержание, медиана / p95 | не закрыто | $/сут (≤ 100 $) | $/сут (полный объём) | $/сут без лучшего круга | $/сут по суткам | медиана итога по суткам, % |")
    print("|" + "---|" * 16)
    for i, (k, v) in enumerate(rows, 1):
        m, coin, x, y, _ = k
        arrow = "↔" if m == "tt" else "→"
        hs = v["hold_s"]
        hold = "—" if hs[0] is None else (f"{hs[0]:.1f} с / {hs[1]:.1f} с" if hs[1] < 120 else f"{hs[0]:.1f} с / {hs[1] / 60:.1f} мин")
        print(f"| {i} | {coin} | {x} {arrow} {y} | {'/'.join(str(e) for e in v['entries'])} | {pc(v['div_med'])} | {pc(v['gross_med'])} | "
              f"{pc(v['net'][0])} / {pc(v['net'][1])} | {v['pos'] * 100:.0f} % | {usd(v['size_med'])} | {hold} | {v['unclosed']} | {usd(v['usd_cap'])} | "
              f"{usd(v['usd_full'])} | {usd(v['usd_cap_trim'])} | {' / '.join(usd(x) for x in v['usd_cap_d'])} | {' / '.join(pc(x) for x in v['net_med_d'])} |")
    print()


if __name__ == "__main__":
    n_top = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    d, R, win_h = main(sys.argv[1])
    for th in (30.0, 50.0, 100.0):
        print_top(R, win_h, "tt", (th, 0, "full"), n_top, True, f"Топ-{n_top}: тейкер-тейкер, θ = {th / 100:g} %, L = 0 мс (мгновенно), полное закрытие — прошли рейтинг")
    for th in (30.0, 50.0):
        print_top(R, win_h, "tt", (th, 50, "full"), n_top, False, f"Топ-{n_top} по $/сут без рейтинга: тейкер-тейкер, θ = {th / 100:g} %, L = 50 мс, полное закрытие (рейтинг не проходит никто)")
    for th in (50.0, 75.0):
        print_top(R, win_h, "mk", (th, 300, "mt"), n_top, True, f"Топ-{n_top}: мейкер + хедж, θ = {th / 100:g} %, L = 300 мс, закрытие мейкером — прошли рейтинг")
    print_top(R, win_h, "mk", (50.0, 300, "mt"), n_top, True, f"Топ-{n_top}: мейкер + хедж, BingX-хедж (вне рейтинга), θ = 0,5 %, L = 300 мс, мейкер", g="bingx")
