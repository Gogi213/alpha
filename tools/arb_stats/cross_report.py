"""Отчёт по cross_run.py: таблицы выходов «к центру» / «через центр» / «переворот», Δ к прежнему выходу «к центру», фандинг, топ-пар. Всё в % (1 bps = 0,01 %) и в $.

Запуск: python3 cross_report.py <cross.pkl> [funding.json] [N=20] > таблицы.md   (текст вокруг — руками)
Рейтинг и плацебо — как в outlier_report: кругов ≥ окно_ч / 6 и медиана чистого итога круга > 0 (плацебо — < 0) в каждых сутках окна (GMT+4).
Чистый итог круга — до фандинга (как в прежнем отчёте); фандинг — отдельной колонкой, «$/сут после фандинга» = $/сут + фандинг (для кругов с известной историей выплат).
"""
import json
import pickle
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from outlier_report import H_LO, hq, pc, placebo, rated, usd  # noqa: E402

TT_ORDER = ("c-t", "x2-t", "x1-t", "flip", "c-m", "x2-m", "x1-m")
MK_ORDER = ("c-t", "x2-t", "x1-t", "c-m", "x2-m", "x1-m")
NAME = {"c-t": "к центру, тейкер (прежний)", "x2-t": "через центр θ/2, тейкер", "x1-t": "через центр θ, тейкер", "flip": "переворот θ, тейкер",
        "c-m": "к центру, мейкер-нога", "x2-m": "через центр θ/2, мейкер-нога", "x1-m": "через центр θ, мейкер-нога"}
HB, HN = 40, 360


def grp(k):
    m, _, x, y, _ = k
    return "bingx" if (("bingx" in (x, y)) if m == "tt" else y == "bingx") else "ok"


def hold_q(h, q):
    c = np.cumsum(h)
    if not c[-1]:
        return None
    return 10 ** ((float(np.searchsorted(c, q * c[-1])) + 0.5) / HB) / 1000       # с


def hold_txt(h):
    a, b = hold_q(h, .5), hold_q(h, .95)
    if a is None:
        return "—"
    f = lambda s: f"{s:.1f} с" if s < 120 else f"{s / 60:.1f} мин" if s < 7200 else f"{s / 3600:.1f} ч"   # noqa: E731
    return f"{f(a)} / {f(b)}"


def aggregate(R, win_h):
    by = defaultdict(lambda: defaultdict(float))
    for k, v in R.items():
        a = by[(k[0], k[-1], grp(k))]
        a["pairs"] += 1
        a["n"] += v["n"]
        a["closed"] += v["closed"]
        a["unclosed"] += v["unclosed"]
        a["usd_cap"] += v["usd_cap"]
        a["usd_full"] += v["usd_full"]
        a["usd_fund"] += v["usd_fund"]
        a["fund_n"] += v["fund_n"]
        a["occ"] += v["occ"]
        a["active"] += v["n"] >= win_h / 6
        a["plac"] += placebo(v, win_h)
        if rated(v, win_h):
            a["rated"] += 1
            a["rated_usd"] += v["usd_cap"]
        for i, x in enumerate(v["usd_cap_d"]):
            a[f"d{i}"] += x
    return by


def table(d, by, mode, lat, g, title):
    Hs = d["hist"]
    order = TT_ORDER if mode == "tt" else MK_ORDER
    print(f"### {title}\n")
    print("| θ | выход | пар с кругами | кругов закрыто | не закрыто, % входов | удержание, медиана / p95 | расхождение на входе, медиана, % | валовый итог, медиана, % | "
          "чистый итог круга, медиана / p25, % | кругов с итогом > 0 | фандинг за удержание, медиана, % | $/сут (≤ 100 $ на круг) | $/сут после фандинга | "
          "занятый номинал, $ (в среднем) | пар с ≥ 1 входом за 6 ч | прошли рейтинг | $/сут прошедших | плацебо |")
    print("|" + "---|" * 18)
    for th in d["thetas"]:
        for ex in order:
            cfg = (th, lat, ex)
            a = by.get((mode, cfg, g))
            if not a:
                continue
            hn, hg, hd, hf, hh = Hs[(mode, cfg, g)]
            tot = hn.sum()
            pos = hn[-H_LO + 1:].sum() / tot if tot else None
            print(f"| {th / 100:g} % | {NAME[ex]} | {a['pairs']:.0f} | {a['closed']:.0f} | {a['unclosed'] / max(a['n'], 1) * 100:.1f} | {hold_txt(hh)} | {pc(hq(hd, .5, 0))} | {pc(hq(hg, .5, H_LO))} | "
                  f"{pc(hq(hn, .5, H_LO))} / {pc(hq(hn, .25, H_LO))} | {'—' if pos is None else f'{pos * 100:.0f} %'} | {pc(hq(hf, .5, H_LO))} | {usd(a['usd_cap'])} | "
                  f"{usd(a['usd_cap'] + a['usd_fund'])} | {usd(a['occ'])} | {a['active']:.0f} | {a['rated']:.0f} | {usd(a['rated_usd'])} | {a['plac']:.0f} |")
    print()


def overview(d, by, mode, g, title):
    """Обзор «θ × выход» на одной странице: $/сут (до фандинга / после), чистый итог круга медиана и % не закрытых, рейтинг / плацебо — по каждой задержке L."""
    order = TT_ORDER if mode == "tt" else MK_ORDER
    lats = d["lats_tt"] if mode == "tt" else d["lats_mk"]
    print(f"### {title}\n")
    for lat in lats:
        print(f"**L = {lat} мс.** Ячейка: $/сут (≤ 100 $ на круг) до фандинга / после · чистый итог круга, медиана, % · не закрыто, % входов · прошли рейтинг / плацебо\n")
        print("| θ | " + " | ".join(NAME[e] for e in order) + " |")
        print("|" + "---|" * (1 + len(order)))
        for th in d["thetas"]:
            cells = []
            for ex in order:
                a = by.get((mode, (th, lat, ex), g))
                if not a:
                    cells.append("—")
                    continue
                hn = d["hist"][(mode, (th, lat, ex), g)][0]
                cells.append(f"{usd(a['usd_cap'])} / {usd(a['usd_cap'] + a['usd_fund'])} · {pc(hq(hn, .5, H_LO))} · {a['unclosed'] / max(a['n'], 1) * 100:.0f} % · {a['rated']:.0f} / {a['plac']:.0f}")
            print(f"| {th / 100:g} % | " + " | ".join(cells) + " |")
        print()


def delta_table(d, by, mode, g, title):
    """Δ $/сут (≤ 100 $) выхода к прежнему «к центру» на тех же θ и L, и по суткам окна (GMT+4)."""
    order = [e for e in (TT_ORDER if mode == "tt" else MK_ORDER) if e != "c-t"]
    lats = d["lats_tt"] if mode == "tt" else d["lats_mk"]
    nd = len(d["days"]) - 1
    print(f"### {title}\n")
    print("| θ | L, мс | «к центру» (прежний), $/сут | " + " | ".join(f"Δ {NAME[e]}" for e in order) + " |")
    print("|" + "---|" * (3 + len(order)))
    for th in d["thetas"]:
        for lat in lats:
            b = by.get((mode, (th, lat, "c-t"), g))
            if not b:
                continue
            cells = []
            for e in order:
                a = by.get((mode, (th, lat, e), g))
                if not a:
                    cells.append("—")
                    continue
                dd = [a[f"d{i}"] - b[f"d{i}"] for i in range(nd)]
                cells.append(f"{usd(a['usd_cap'] - b['usd_cap'])} ({sum(x > 0 for x in dd)}/{nd})")
            print(f"| {th / 100:g} % | {lat} | {usd(b['usd_cap'])} | " + " | ".join(cells) + " |")
    print(f"\nВ скобках — в скольких из {nd} суток окна (GMT+4; первые сутки неполные — 13,8 ч) сумма $/сут выхода выше прежнего «к центру».\n")


def per_day(d, by, mode, lat, g, title):
    nd = len(d["days"]) - 1
    order = TT_ORDER if mode == "tt" else MK_ORDER
    print(f"### {title}\n")
    print("| θ | выход | " + " | ".join(f"сутки {i + 1}" for i in range(nd)) + " |")
    print("|" + "---|" * (2 + nd))
    for th in d["thetas"]:
        for ex in order:
            a = by.get((mode, (th, lat, ex), g))
            if a:
                print(f"| {th / 100:g} % | {NAME[ex]} | " + " | ".join(usd(a[f"d{i}"]) for i in range(nd)) + " |")
    print()


def top(R, win_h, mode, cfg, n, g="ok", only_rated=True):
    rows = [(k, v) for k, v in R.items() if k[0] == mode and k[-1] == cfg and grp(k) == g and (not only_rated or rated(v, win_h))]
    rows.sort(key=lambda kv: -kv[1]["usd_cap"])
    return rows[:n], len(rows)


def print_top(R, win_h, mode, cfg, n, title, g="ok", only_rated=True):
    rows, tot = top(R, win_h, mode, cfg, n, g, only_rated)
    print(f"### {title}\n")
    print(f"Пар в списке: {tot}; показаны первые {len(rows)}; сумма показанных = {usd(sum(v['usd_cap'] for _, v in rows))} $/сут (≤ 100 $ на круг), без лучшего круга каждой пары = "
          f"{usd(sum(v['usd_cap_trim'] for _, v in rows))}.\n")
    print("| # | монета | пара | входов 1ч/4ч/6ч/1сут | расхождение на входе, медиана, % | валовый итог, медиана, % | чистый итог, медиана / p25, % | кругов с итогом > 0 | "
          "размер, медиана, $ | удержание, медиана / p95 | не закрыто | фандинг, медиана, % | $/сут (≤ 100 $) | $/сут после фандинга | $/сут без лучшего круга | $/сут по суткам | медиана итога по суткам, % |")
    print("|" + "---|" * 17)
    for i, (k, v) in enumerate(rows, 1):
        m, coin, x, y, _ = k
        hs = v["hold_s"]
        hold = "—" if hs[0] is None else (f"{hs[0]:.1f} с / {hs[1]:.1f} с" if hs[1] < 120 else f"{hs[0]:.1f} с / {hs[1] / 60:.1f} мин")
        print(f"| {i} | {coin} | {x} {'↔' if m == 'tt' else '→'} {y} | {'/'.join(str(e) for e in v['entries'])} | {pc(v['div_med'])} | {pc(v['gross_med'])} | "
              f"{pc(v['net'][0])} / {pc(v['net'][1])} | {v['pos'] * 100:.0f} % | {usd(v['size_med'])} | {hold} | {v['unclosed']} | {pc(v['fund_med'])} | {usd(v['usd_cap'])} | "
              f"{usd(v['usd_cap'] + v['usd_fund'])} | {usd(v['usd_cap_trim'])} | {' / '.join(usd(x) for x in v['usd_cap_d'])} | {' / '.join(pc(x) for x in v['net_med_d'])} |")
    print()


def funding_summary(path):
    f = json.load(open(path))
    print("### Фандинг: история выплат и интервалы по символам\n")
    print("| биржа | символов с историей | без истории | интервал 1 ч | 2 ч | 4 ч | 8 ч | другой | ставка за выплату, медиана / p95 модуля, % |")
    print("|---|---|---|---|---|---|---|---|---|")
    for ex, m in f["rates"].items():
        iv = defaultdict(int)
        rr = []
        for s, rows in m.items():
            t = np.array([r[0] for r in rows], float)
            rr += [abs(r[1]) for r in rows]
            if len(t) < 2:
                iv["другой"] += 1
                continue
            h = round(float(np.median(np.diff(t))) / 3.6e6, 2)
            iv[h if h in (1, 2, 4, 8) else "другой"] += 1
        rr = np.array(rr) * 100
        print(f"| {ex} | {len(m)} | {len(f['missing'][ex])} | {iv[1]} | {iv[2]} | {iv[4]} | {iv[8]} | {iv['другой']} | "
              f"{np.median(rr):.4f} / {np.percentile(rr, 95):.4f} |")
    print()


def main(path, fpath=None, n_top=20, only=None):
    d = pickle.load(open(path, "rb"))
    R = d["rows"]
    win_h = (d["t1"] - d["t0"]) / 3.6e6
    by = aggregate(R, win_h)
    print(f"Окно {win_h:.2f} ч; строк (пара × настройка): {len(R)}; пар tt: {len({k[:4] for k in R if k[0] == 'tt'})}, mk: {len({k[:4] for k in R if k[0] == 'mk'})}\n")
    for mode, mt in (("tt", "Тейкер-тейкер вход"), ("mk", "Мейкер + хедж вход")):
        lats = d["lats_tt"] if mode == "tt" else d["lats_mk"]
        for g, gt in (("ok", "без BingX"), ("bingx", "BingX (вне рейтинга)")):
            overview(d, by, mode, g, f"{mt}: обзор — {gt}")
            for lat in lats:
                table(d, by, mode, lat, g, f"{mt}, L = {lat} мс — {gt}")
            delta_table(d, by, mode, g, f"{mt}: Δ $/сут к прежнему «к центру» — {gt}")
    for lat in d["lats_tt"]:
        per_day(d, by, "tt", lat, "ok", f"Тейкер-тейкер вход, L = {lat} мс — $/сут по суткам окна (без BingX)")
    if fpath:
        funding_summary(fpath)
    return d, R, win_h


if __name__ == "__main__":
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 20
    d, R, win_h = main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None, n)
