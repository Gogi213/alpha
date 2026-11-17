"""Дополнение к tokyo_report: число РАЗНЫХ пар (монета × биржи) с L* ≥ 5 / ≥ 10 мс (объединение по θ и выходам), по парам бирж; $/сут по суткам в ключевых ячейках.

Запуск: python3 tokyo_extra.py <tokyo.pkl>  — печатает markdown в stdout. Только группа «без BingX» (BingX — снимки ≈ 2,6/с и метки E с отрицательным лагом: мс-масштаб не заявляется).
"""
import pickle
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from tokyo_report import by_cfg, lstar, grp  # noqa: E402


def main(pkl):
    d = pickle.load(open(pkl, "rb"))
    win_h = (d["t1"] - d["t0"]) / 3_600_000
    lats = d["lats"]
    agg, per = by_cfg(d)
    print("### Разные пары (монета × биржи), у которых хотя бы в одной настройке (θ × выход) безубыточная задержка L* подряд ≥ порога; без BingX\n")
    print("Объединение по θ и выходам — это выбор по максимуму внутри пары (число настроек на пару: тейкер-тейкер 14, мейкер-вход 28); плацебо — те же условия с обратным знаком.\n")
    print("| режим | критерий | L* ≥ 1 мс | ≥ 5 мс | ≥ 10 мс | ≥ 20 мс | по парам бирж, ≥ 5 мс | по парам бирж, ≥ 10 мс |")
    print("|---|---|---|---|---|---|---|---|")
    for mode, dm in (("tt", "-"), ("mk", "L"), ("mk", "300")):
        for kind, kn in (("sum", "сумма > 0"), ("rated", "рейтинг")):
            sets = {t: set() for t in (1, 5, 10, 20)}
            byex = {5: defaultdict(set), 10: defaultdict(set)}
            for k, ser in per.items():
                m, coin, x, y, th, ex, dmm = k
                if m != mode or dmm != dm or grp(m, x, y) != "ok":
                    continue
                _, ct = lstar(ser, lats, win_h, kind)
                if ct is None:
                    continue
                pk = (coin, *sorted((x, y))) if mode == "tt" else (coin, x, y)
                for t in sets:
                    if ct >= t:
                        sets[t].add(pk)
                for t in (5, 10):
                    if ct >= t:
                        byex[t][f"{pk[1]}–{pk[2]}" if mode == "tt" else f"{x}→{y}"].add(coin)
            f = lambda t: "; ".join(f"{e}: {len(s)}" for e, s in sorted(byex[t].items(), key=lambda a: -len(a[1]))) or "—"   # noqa: E731
            nm = {"tt": "тейкер-тейкер", "mk": f"мейкер-вход, D = {dm if dm != 'L' else 'L'}"}[mode]
            print(f"| {nm} | {kn} | " + " | ".join(str(len(sets[t])) for t in (1, 5, 10, 20)) + f" | {f(5)} | {f(10)} |")
    print("\n### $/сут по суткам GMT+4 (сумма по парам без BingX; сутки: 13.11 неполные 13,8 ч · 14.11 · 15.11 · 16.11 до 14:04 GMT+4, 14,07 ч)\n")
    print("| режим | выход | D | θ, % | L, мс | $/сут всего | по суткам | суток с плюсом | прошли рейтинг / плацебо | занятый номинал, $ |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for mode, dm in (("tt", "-"), ("mk", "L"), ("mk", "300")):
        for ex in ("c-t", "x1-t"):
            for th in (15.0, 20.0, 30.0, 50.0):
                for L in (1, 2, 5, 10):
                    rs = agg.get((mode, "ok", th, ex, dm), {}).get(L, [])
                    if not rs:
                        continue
                    days = np.sum([r["usd_cap_d"] for r in rs], axis=0)
                    from outlier_report import placebo, rated
                    print(f"| {mode} | {ex} | {dm} | {th / 100:g} | {L} | {sum(r['usd_cap'] for r in rs):+.0f} | " + " / ".join(f"{x:+.0f}" for x in days)
                          + f" | {int((days > 0).sum())} из {len(days)} | {sum(rated(r, win_h) for r in rs)} / {sum(placebo(r, win_h) for r in rs)} | {sum(r['occ'] for r in rs):.0f} |")


if __name__ == "__main__":
    main(sys.argv[1])
