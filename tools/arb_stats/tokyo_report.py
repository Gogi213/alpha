"""Таблицы отчёта arb-tokyo-latency по tokyo_run.py: жизнь расхождения, $/сут и рейтинг по задержке L, безубыточная задержка L* по парам, сдвиг часов.

Запуск: python3 tokyo_report.py <tokyo.pkl> <каталог выхода> [shift.pkl]   — пишет tables.md и pairs.csv (настройки с L* подряд (рейтинг) ≥ 5 мс) и life.csv (жизнь расхождения по парам).
Всё в % (1 bps = 0,01 %) и в $. Рейтинг и плацебо — как в outlier_report: кругов ≥ окно_ч / 6 и медиана чистого итога круга > 0 (плацебо — < 0) в каждых сутках окна (GMT+4).
L*: «сумма» — наибольшая L сетки, при которой n ≥ окно_ч / 6 и чистая сумма по кругам > 0; «рейтинг» — то же с условием рейтинга; «подряд» — плюс при всех L сетки от 1 мс до L (L = 0 не считается: мгновенное исполнение).
"""
import csv
import pickle
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from outlier_report import placebo, rated  # noqa: E402

LIFE_BPD = 40
EXN = {"binance": "Binance", "bybit": "Bybit", "okx": "OKX", "bingx": "BingX"}


def grp(mode, x, y):
    return "bingx" if (("bingx" in (x, y)) if mode == "tt" else ("bingx" in (x, y))) else "ok"


def hq(h, q):
    c = np.cumsum(h)
    return float(10 ** ((np.searchsorted(c, q * c[-1]) + 0.5) / LIFE_BPD)) if c[-1] else None


def fmt_ms(x):
    return "—" if x is None else (f"{x:.0f}" if x >= 10 else f"{x:.1f}")


def life_tables(d, out):
    lats = d["lats"]
    pool = defaultdict(lambda: {"h": 0, "n": 0, "cens": 0, "surv": np.zeros(len(lats)), "pairs": 0})
    for (coin, a, b), r in d["life"].items():
        for th, v in r["th"].items():
            for k in ((a, b), ("все", "все")):
                p = pool[(k, th)]
                p["h"] = p["h"] + v["hist"]
                p["n"] += v["n"]
                p["cens"] += v["cens"]
                p["surv"] += np.array(v["surv"]) * v["n"]
                p["pairs"] += 1
    print("### Жизнь расхождения ≥ θ, мс (все пары монет × биржи; эпизод — от первого момента расхождения до первого момента без него)\n", file=out)
    print("| пара бирж | θ, % | пар с эпизодами | эпизодов | p10 / p50 / p90, мс | дольше " + " / ".join(f"{L}" for L in lats[1:]) + " мс, % эпизодов |", file=out)
    print("|---|---|---|---|---|---|", file=out)
    for k in sorted({k for k, _ in pool}, key=lambda k: (k[0] == "все", k)):
        for th in d["thetas"]:
            p = pool.get((k, th))
            if not p or not p["n"]:
                continue
            nm = "все пары" if k[0] == "все" else f"{EXN[k[0]]}–{EXN[k[1]]}"
            sv = p["surv"] / p["n"]
            print(f"| {nm} | {th / 100:g} | {p['pairs']} | {p['n']} | {fmt_ms(hq(p['h'], .1))} / {fmt_ms(hq(p['h'], .5))} / {fmt_ms(hq(p['h'], .9))} | "
                  + " / ".join(f"{100 * x:.1f}" for x in sv[1:]) + " |", file=out)
    print(file=out)
    vs = [r["valid_share"] for r in d["life"].values()]
    print(f"Доля времени ряда пары с обеими ногами не старше {d['cap_ms'] // 1000} с: медиана по парам {100 * np.median(vs):.2f} %, p10 {100 * np.percentile(vs, 10):.2f} %, "
          f"min {100 * min(vs):.2f} %.\n", file=out)


def by_cfg(d):
    """{(режим, группа, θ, выход, dmode): {L: [сводки по парам]}} и {(режим, пара, θ, выход, dmode): {L: сводка}}."""
    agg, per = defaultdict(lambda: defaultdict(list)), defaultdict(dict)
    for k, v in d["rows"].items():
        mode, coin, x, y, (th, lat, ex, dm) = k
        agg[(mode, grp(mode, x, y), th, ex, dm)][lat].append(v)
        per[(mode, coin, x, y, th, ex, dm)][lat] = v
    return agg, per


def usd_tables(d, agg, out, win_h):
    lats = d["lats"]
    names = {"c-t": "к центру", "x1-t": "«из − в +» (через центр θ)"}
    dms = {"-": "", "L": ", период котировки D = L", "300": ", период котировки D = 300 мс"}
    for mode in ("tt", "mk"):
        for g in ("ok", "bingx"):
            for dm in (("-",) if mode == "tt" else ("L", "300")):
                for ex in ("c-t", "x1-t"):
                    title = ("тейкер-тейкер" if mode == "tt" else "мейкер-вход (Viking) + хедж") + f", {names[ex]}{dms[dm]}, группа «{'без BingX' if g == 'ok' else 'с BingX'}»"
                    print(f"#### {title}\n", file=out)
                    print("Ячейка: $/сут (≤ 100 $ на круг) · прошли рейтинг / плацебо · пар с кругами\n", file=out)
                    print("| θ, % | " + " | ".join(f"L = {L} мс" for L in lats) + " |", file=out)
                    print("|---|" + "---|" * len(lats), file=out)
                    for th in d["thetas"]:
                        cells = []
                        for L in lats:
                            rs = agg.get((mode, g, th, ex, dm), {}).get(L, [])
                            if not rs:
                                cells.append("—")
                                continue
                            cells.append(f"{sum(r['usd_cap'] for r in rs):+.0f} · {sum(rated(r, win_h) for r in rs)}/{sum(placebo(r, win_h) for r in rs)} · {len(rs)}")
                        print(f"| {th / 100:g} | " + " | ".join(cells) + " |", file=out)
                    print(file=out)


def lstar(ser, lats, win_h, kind):
    """ser {L: сводка} -> (L* наибольшая, L* подряд); kind 'sum' | 'rated' | 'plac'."""
    def ok(v):
        if v is None or v["n"] < win_h / 6:
            return False
        if kind == "sum":
            return v["usd_cap"] > 0
        return rated(v, win_h) if kind == "rated" else placebo(v, win_h)
    pos = {L: ok(ser.get(L)) for L in lats}
    mx = max([L for L in lats if L >= 1 and pos[L]], default=None)
    ct = None
    for L in lats:
        if L < 1:
            continue
        if not pos[L]:
            break
        ct = L
    return mx, ct


def lstar_tables(d, per, out, win_h):
    lats = d["lats"]
    cnt = defaultdict(lambda: defaultdict(int))
    exn = defaultdict(lambda: defaultdict(int))
    rows_csv = []
    for k, ser in per.items():
        mode, coin, x, y, th, ex, dm = k
        g = grp(mode, x, y)
        for kind in ("sum", "rated", "plac"):
            mx, ct = lstar(ser, lats, win_h, kind)
            for thr in (1, 5, 10):
                if ct is not None and ct >= thr:
                    cnt[(mode, g, th, ex, dm, kind)][f"ct{thr}"] += 1
                if mx is not None and mx >= thr:
                    cnt[(mode, g, th, ex, dm, kind)][f"mx{thr}"] += 1
                if kind == "rated" and g == "ok" and ct is not None and ct >= thr:
                    exn[(thr, mode, dm, ex)][f"{x}–{y}"] += 1
        mx, ct = lstar(ser, lats, win_h, "sum")
        mr, cr = lstar(ser, lats, win_h, "rated")
        if cr is not None and cr >= 5 and g == "ok":
            u = {L: (ser[L]["usd_cap"] if L in ser else None) for L in (1, 5, 10)}
            rows_csv.append([mode, coin, x, y, th / 100, ex, dm, g, ct, mx, cr, mr] + [None if u[L] is None else round(u[L], 2) for L in (1, 5, 10)]
                            + [ser[1]["n"] if 1 in ser else None, ser[1]["net_med_d"] if 1 in ser else None, ser[1]["usd_cap_d"] if 1 in ser else None])
    print("### Безубыточная задержка L* — сколько пар (монета × биржи) имеют L* ≥ 1 / 5 / 10 мс\n", file=out)
    print("Пара считается по каждой настройке отдельно (θ, выход); «подряд» — плюс при всех L от 1 мс до L*. «сумма» — чистая сумма кругов > 0 при n ≥ окно_ч/6; «рейтинг» — как рейтинг; «плацебо» — те же условия с обратным знаком.\n", file=out)
    print("| режим | группа | θ, % | выход | D | L* подряд ≥ 1 / 5 / 10 (сумма) | то же (рейтинг) | то же (плацебо) | L* наибольшая ≥ 5 / 10 (сумма) |", file=out)
    print("|---|---|---|---|---|---|---|---|---|", file=out)
    for key in sorted({k[:5] for k in cnt}):
        mode, g, th, ex, dm = key
        c = lambda kind, p: cnt[(*key, kind)][p]   # noqa: E731
        print(f"| {mode} | {g} | {th / 100:g} | {ex} | {dm} | " + " / ".join(str(c("sum", f"ct{t}")) for t in (1, 5, 10)) + " | "
              + " / ".join(str(c("rated", f"ct{t}")) for t in (1, 5, 10)) + " | " + " / ".join(str(c("plac", f"ct{t}")) for t in (1, 5, 10)) + " | "
              + " / ".join(str(c("sum", f"mx{t}")) for t in (5, 10)) + " |", file=out)
    print(file=out)
    print("#### На каких биржах пары с L* (рейтинг, подряд) ≥ 5 мс / ≥ 10 мс — по парам бирж, все настройки\n", file=out)
    print("| порог | режим | D | выход | пары бирж: число (монета × θ) |", file=out)
    print("|---|---|---|---|---|", file=out)
    for key in sorted(exn):
        thr, mode, dm, ex = key
        if thr == 1:
            continue
        print(f"| ≥ {thr} мс | {mode} | {dm} | {ex} | " + ", ".join(f"{p}: {n}" for p, n in sorted(exn[key].items(), key=lambda t: -t[1])) + " |", file=out)
    print(file=out)
    return rows_csv


def top_tables(d, per, out, win_h, N=12):
    lats = d["lats"]
    print("### Лучшие пары по $/сут при L = 5 и 10 мс (рейтинг при этом L; без BingX)\n", file=out)
    for L in (5, 10):
        rowsx = []
        for k, ser in per.items():
            mode, coin, x, y, th, ex, dm = k
            v = ser.get(L)
            if v is None or grp(mode, x, y) != "ok" or not rated(v, win_h):
                continue
            rowsx.append((v["usd_cap"], k, v, ser))
        rowsx.sort(key=lambda t: -t[0])
        print(f"**L = {L} мс**: прошли рейтинг {len(rowsx)} настроек (пара × θ × выход × D); топ-{N}\n", file=out)
        print("| режим | монета | пара | θ, % | выход | D | $/сут при L = 1 / 5 / 10 | $/сут по суткам (L) | кругов | чистый итог круга, медиана, % | L* подряд (рейтинг) |", file=out)
        print("|---|---|---|---|---|---|---|---|---|---|---|", file=out)
        for u, k, v, ser in rowsx[:N]:
            mode, coin, x, y, th, ex, dm = k
            g = lambda LL: f"{ser[LL]['usd_cap']:+.1f}" if LL in ser else "—"   # noqa: E731
            print(f"| {mode} | {coin} | {EXN[x]}→{EXN[y]} | {th / 100:g} | {ex} | {dm} | {g(1)} / {g(5)} / {g(10)} | " + " / ".join(f"{z:+.1f}" for z in v["usd_cap_d"])
                  + f" | {v['n']} | {v['net'][0] / 100:.3f} | {lstar(ser, lats, win_h, 'rated')[1]} |", file=out)
        tot = sum(t[0] for t in rowsx)
        top10 = sum(t[0] for t in rowsx[:10])
        print(f"\nСумма $/сут прошедших: {tot:+.0f}; доля топ-10: {100 * top10 / tot:.0f} %\n" if tot > 0 else "\n", file=out)


def shift_tables(ds, out, win_h):
    print("### Чувствительность к сдвигу часов: время E второй биржи пары сдвинуто на s мс (тейкер-тейкер, без BingX, выборка монет)\n", file=out)
    d = ds
    agg = defaultdict(lambda: defaultdict(list))
    for k, v in d["rows"].items():
        mode, coin, x, y, (th, lat, ex, tag) = k
        if grp(mode, x, y) == "ok":
            agg[(th, ex, tag)][lat].append(v)
    for ex in ("c-t", "x1-t"):
        print(f"**выход {ex}**; ячейка: $/сут · прошли рейтинг/плацебо\n", file=out)
        for th in d["thetas"]:
            tags = sorted({t for (a, e, t) in agg if a == th and e == ex}, key=lambda s: int(s[1:]))
            if not tags:
                continue
            print(f"θ = {th / 100:g} %\n", file=out)
            print("| сдвиг s, мс | " + " | ".join(f"L = {L}" for L in d["lats"]) + " |", file=out)
            print("|---|" + "---|" * len(d["lats"]), file=out)
            for t in tags:
                cells = []
                for L in d["lats"]:
                    rs = agg[(th, ex, t)].get(L, [])
                    cells.append("—" if not rs else f"{sum(r['usd_cap'] for r in rs):+.0f} · {sum(rated(r, win_h) for r in rs)}/{sum(placebo(r, win_h) for r in rs)}")
                print(f"| {t[1:]} | " + " | ".join(cells) + " |", file=out)
            print(file=out)


def main(pkl, outdir, shift=None):
    d = pickle.load(open(pkl, "rb"))
    win_h = (d["t1"] - d["t0"]) / 3_600_000
    od = Path(outdir)
    od.mkdir(parents=True, exist_ok=True)
    with open(od / "tables.md", "w", encoding="utf-8") as out:
        print(f"Окно {win_h:.2f} ч, суток GMT+4: {len(d['days']) - 1}, монет {d['coins']}, строк сводок {len(d['rows'])}, пар с жизнью {len(d['life'])}.\n", file=out)
        if d["life"]:
            life_tables(d, out)
        if d["rows"]:
            agg, per = by_cfg(d)
            usd_tables(d, agg, out, win_h)
            rows_csv = lstar_tables(d, per, out, win_h)
            top_tables(d, per, out, win_h)
            with open(od / "pairs.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["режим", "монета", "нога 1", "нога 2", "θ, %", "выход", "D", "группа", "L* подряд (сумма), мс", "L* наибольшая (сумма), мс", "L* подряд (рейтинг), мс",
                            "L* наибольшая (рейтинг), мс", "$/сут L=1", "$/сут L=5", "$/сут L=10", "кругов L=1", "медиана круга по суткам L=1, bps", "$/сут по суткам L=1"])
                w.writerows(rows_csv)
        if d["life"]:
            with open(od / "life.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["монета", "нога 1", "нога 2", "θ, %", "эпизодов", "p10, мс", "p50, мс", "p90, мс"] + [f"дольше {L} мс, %" for L in d["lats"][1:]] + ["доля времени с расхождением, %"])
                for (coin, a, b), r in sorted(d["life"].items()):
                    for th, v in sorted(r["th"].items()):
                        w.writerow([coin, a, b, th / 100, v["n"]] + [round(x, 1) for x in v["q"]] + [round(100 * x, 1) for x in v["surv"][1:]] + [round(100 * v["share_t"], 3)])
        if shift:
            shift_tables(pickle.load(open(shift, "rb")), out, win_h)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
