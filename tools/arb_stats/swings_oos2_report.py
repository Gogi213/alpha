"""Таблицы отчёта по преддекларации: допуск, задержка обнаружения, разработка, финал (тестовые сутки), плацебо, флаг, топ-30.

Запуск: python3 swings_oos2_report.py <swings2.pkl> <oos2_dev.pkl> <выход_каталог> [<oos2_final.pkl>]
Без финала — только разработка. Десятичная точка в числах кода; в тексте отчёта заменяется вручную.
"""
import csv
import pickle
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import swings  # noqa: E402
import swings_oos2 as so  # noqa: E402

NS, LS, HS, ALLP, PREDS = so.NS, so.LS, so.HS, so.ALLP, so.PREDS
GN = ("ok", "bx")
FEE_TT, FEE_MT, FEE_MM = 0.126, 0.1071, 0.0882
OUT = []


def p(s=""):
    OUT.append(s)


def f(x, d=2):
    return "—" if x is None or not np.isfinite(x) else f"{x:.{d}f}"


def adm_table(dv):
    p("### Допуск пар·N (по разработке)")
    p("Допустимо, если N ≥ max(3 шага цены, G) и 2N > 0,126 %; G = 2 · медиана полуспреда hm на разработке (мин), шаг цены — максимум по двум биржам пары. «N/G» — медиана по допустимым парам.")
    p("| группа | N, % | пар | допустимо | G, %: p10 / p50 / p90 | шаг цены, %: p50 / p90 | N/G, медиана | допустимых с ≥ 1 качелей (L = 300 мс / 10 с) к t_split |")
    p("|---|---|---|---|---|---|---|---|")
    for g in (0, 1):
        m = dv["grp"] == g
        for iN, N in enumerate(NS):
            a = m & dv["adm"][:, iN]
            Gq = np.percentile(dv["G"][m], [10, 50, 90])
            tq = np.percentile(dv["tick"][m], [50, 90])
            act = []
            for L in LS:
                act.append(sum(1 for q in np.flatnonzero(a) if len(SW[(q, iN, LS.index(L))]) and SW[(q, iN, LS.index(L))][0] <= dv["Ts"]))
            p(f"| {GN[g]} | {N} | {int(m.sum())} | {int(a.sum())} | {f(Gq[0], 3)} / {f(Gq[1], 3)} / {f(Gq[2], 3)} | {f(tq[0], 4)} / {f(tq[1], 4)} | {f(np.median(N / dv['G'][a]) if a.any() else np.nan, 1)} | {act[0]} / {act[1]} |")
    p()


def delay_table(dv):
    p("### Задержка обнаружения режима потока (BOCPD по интервалам, только разработка)")
    p(f"λ = {dv['lam_sw']} интервалов (выбрана по суммарному правдоподобию на 200 рядах разработки: " + ", ".join(f"{k if k < 10 ** 8 else '∞'}: {v:.0f}" for k, v in sorted(dv["evidence"].items())) + "). "
      "Задержка — длина нового режима (интервалов качелей и часов) в момент, когда MAP-длина режима упала на ≥ 3; медиана [p25; p75]. Режим берётся в предикторе reg, если возраст ≥ медиана + 3 интервала и ≥ медианы в часах.")
    p("| группа | N, % | L | рядов (≥ 30 качелей на разработке) | падений | задержка, интервалов | задержка, ч |")
    p("|---|---|---|---|---|---|---|")
    for g in (0, 1):
        for iN, N in enumerate(NS):
            for iL, L in enumerate(LS):
                t = dv["delay_table"].get((g, iN, iL))
                if not t:
                    continue
                d, h = t["d_int"], t["d_h"]
                p(f"| {GN[g]} | {N} | {L} мс | {t['series']} | {t['drops']} | {f(d[1], 0)} [{f(d[0], 0)}; {f(d[2], 0)}] | {f(h[1], 2)} [{f(h[0], 2)}; {f(h[2], 2)}] |")
    p()


def score_table(title, sc, dv, popn):
    p(f"### {title}")
    p("Среднее по точкам отсчёта кросс-секционного Спирмена (предиктор, число качелей в (t, t + H]) по допустимым парам с ≥ 1 качелей до t; клетка считается при ≥ 30 парах. "
      "Формат ячейки: naive24 / glob / win6 / reg. Пар — в первой точке отсчёта.")
    p("| группа | N, % | N/G | L | пар | H = 1 ч | H = 6 ч | H = 12 ч | H = 24 ч |")
    p("|---|---|---|---|---|---|---|---|---|")
    for g in (0, 1):
        for iN, N in enumerate(NS):
            a = (dv["grp"] == g) & dv["adm"][:, iN]
            ng = np.median(N / dv["G"][a]) if a.any() else np.nan
            for iL, L in enumerate(LS):
                n0 = sc[(g, iN, iL, 0, 0)][2]
                if n0 < so.MIN_POP:
                    continue
                cells = []
                for ih in range(len(HS)):
                    cells.append(" / ".join(f(sc[(g, iN, iL, ih, ip)][0]) for ip in range(len(ALLP))))
                p(f"| {GN[g]} | {N} | {f(ng, 1)} | {L} мс | {n0} | " + " | ".join(cells) + " |")
    p()


def flag_table(title, fl):
    p(f"### {title}")
    p("Часовые точки (каждая 2-я точка отсчёта), горизонт 1 ч; флаг — предсказанная частота ≥ 1 качели/ч; «точность» — доля флагов с ≥ 1 качелей в следующий час; база — та же доля по всем допустимым парам с ≥ 1 качелей до t. Формат: флагов / точность / подъём.")
    p("| группа | N, % | L | база | naive24 | glob | win6 | reg |")
    p("|---|---|---|---|---|---|---|---|")
    for g in (0, 1):
        for iN, N in enumerate(NS):
            for iL, L in enumerate(LS):
                row = [fl[(g, iN, iL, ip)] for ip in range(len(ALLP))]
                if all(r[0] == 0 for r in row):
                    continue
                p(f"| {GN[g]} | {N} | {L} мс | {f(row[0][2])} | " + " | ".join(f"{r[0]} / {f(r[1])} / {f(r[3])}" for r in row) + " |")
    p()


def thresholds(fi):
    """Пороги плацебо: p95 по репликам от максимума (по допустимым клеткам, предикторам glob/win6/reg), и буквальный вариант — максимум по клеткам от p95."""
    cl = [c for c in fi["score"] if c[4] > 0 and fi["score"][c][2] >= so.MIN_POP and np.isfinite(fi["score"][c][0])]
    out = {}
    for name in ("shuf", "phase"):
        R = sorted(fi[name])
        M = np.array([[fi[name][r][c][0] for c in cl] for r in R], float)
        mx = np.nanmax(M, axis=1)
        out[name] = {"maxT95": float(np.nanpercentile(mx, 95)), "literal": float(np.nanmax(np.nanpercentile(M, 95, axis=0))), "R": len(R), "cells": cl, "M": M}
    return cl, out


def verdict(fi):
    cl, th = thresholds(fi)
    theta = max(th["shuf"]["maxT95"], th["phase"]["maxT95"])
    p("### Финал: тестовые сутки — клетки, пороги, вердикт по преддекларации")
    p(f"Порог плацебо θ = max(p95 по репликам от максимума по {len(cl)} допустимым клеткам): «пары» (R = {th['shuf']['R']}) {f(th['shuf']['maxT95'], 3)}, фаз-рандомизованное (R = {th['phase']['R']}) {f(th['phase']['maxT95'], 3)}; "
      f"буквальный вариант (максимум по клеткам от p95): {f(th['shuf']['literal'], 3)} / {f(th['phase']['literal'], 3)} (он не выше). θ = {f(theta, 3)}. Успех клетки: (i) Спирмен > naive24, (ii) > θ, (iii) ≥ 30 допустимых пар с ≥ 1 качелей, (iv) ≥ 0,2.")
    rows = []
    for c in cl:
        g, iN, iL, ih, ip = c
        s, n, n0 = fi["score"][c]
        base = fi["score"][(g, iN, iL, ih, 0)][0]
        pl = np.nanpercentile(th["phase"]["M"][:, cl.index(c)], 95)
        ok = [s > base, s > theta, n0 >= so.MIN_POP, s >= 0.2]
        rows.append((c, s, base, pl, ok, n0))
    win = [r for r in rows if all(r[4])]
    p(f"Клеток проверено: {len(rows)}; **успешных: {len(win)}**; пройдено (i) / (ii) / (iv) отдельно: {sum(r[4][0] for r in rows)} / {sum(r[4][1] for r in rows)} / {sum(r[4][3] for r in rows)}.")
    p("| группа | N, % | L | H, ч | предиктор | пар | Спирмен | naive24 | плацебо-фаз. p95 (клетка) | (i) | (ii) | (iv) | успех |")
    p("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for c, s, base, pl, ok, n0 in sorted(rows, key=lambda r: -r[1])[:40]:
        g, iN, iL, ih, ip = c
        p(f"| {GN[g]} | {NS[iN]} | {LS[iL]} мс | {HS[ih]} | {ALLP[ip]} | {n0} | {f(s, 3)} | {f(base, 3)} | {f(pl, 3)} | {'да' if ok[0] else 'нет'} | {'да' if ok[1] else 'нет'} | {'да' if ok[3] else 'нет'} | {'**да**' if all(ok) else 'нет'} |")
    p("(в таблице — 40 клеток с наибольшим Спирменом; полный список — `final-cells.csv`)")
    p()
    with open(Path(OUTDIR, "final-cells.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["группа", "N %", "L мс", "H ч", "предиктор", "пар", "Спирмен", "naive24", "плацебо фаз p95 клетки", "(i)", "(ii)", "(iii)", "(iv)", "успех"])
        for c, s, base, pl, ok, n0 in rows:
            g, iN, iL, ih, ip = c
            w.writerow([GN[g], NS[iN], LS[iL], HS[ih], ALLP[ip], n0, round(s, 4), round(base, 4), round(pl, 4)] + ok + [all(ok)])
    # флаги
    fcells = [(g, iN, iL, ip) for g in (0, 1) for iN in range(len(NS)) for iL in range(len(LS)) for ip in range(1, len(ALLP)) if fi["flags"][(g, iN, iL, ip)][0] >= so.MIN_POP]
    mx = []
    for r in sorted(fi["pflag"]):
        v = [fi["pflag"][r][c][3] for c in fcells if fi["pflag"][r][c][0] >= so.MIN_POP and np.isfinite(fi["pflag"][r][c][3])]
        if v:
            mx.append(max(v))
    thf = float(np.percentile(mx, 95)) if len(mx) >= 10 else np.nan
    p("### Финал: флаг «≥ 1 качели/ч» против плацебо")
    p(f"Успех флага (преддекларация): подъём ≥ 1,5 при ≥ 30 флагах, больше подъёма naive24 в той же клетке и больше θ_флаг = p95 по репликам фаз-плацебо от максимума подъёма по {len(fcells)} клеткам флага = {f(thf)} (реплик с флагами: {len(mx)}).")
    p("| группа | N, % | L | предиктор | флагов | точность | база | подъём | подъём naive24 | плацебо-фаз. p95 подъёма (клетка) | успех |")
    p("|---|---|---|---|---|---|---|---|---|---|---|")
    nwin = 0
    for c in fcells:
        g, iN, iL, ip = c
        n, prec, base, lift = fi["flags"][c]
        lift0 = fi["flags"][(g, iN, iL, 0)][3]
        pl = [fi["pflag"][r][c][3] for r in fi["pflag"] if fi["pflag"][r][c][0] >= so.MIN_POP and np.isfinite(fi["pflag"][r][c][3])]
        ok = lift >= 1.5 and (not np.isfinite(lift0) or lift > lift0) and (not np.isfinite(thf) or lift > thf)
        nwin += ok
        p(f"| {GN[g]} | {NS[iN]} | {LS[iL]} мс | {ALLP[ip]} | {n} | {f(prec)} | {f(base)} | {f(lift)} | {f(lift0)} | {f(np.percentile(pl, 95) if len(pl) >= 10 else np.nan)} ({len(pl)}) | {'**да**' if ok else 'нет'} |")
    p(f"Клеток флага с ≥ 30 флагами и успехом: **{nwin}**.")
    p()
    return rows, th


OUTDIR = "."
SW = {}


def top30(D, fi, rows):
    """Топ-30 по числу подтверждённых качелей на тестовых сутках среди флагнутых часов reg (≥ 1 качели/ч): ранжирование — по исходу тестовых суток, найдено на этих данных."""
    import swings_oos_report as old
    old.OUTDIR = OUTDIR
    p("### Топ-30 пар (тестовые сутки; флаг reg ≥ 1 качели/ч, горизонт 1 ч, часовые точки)")
    p("Ранжирование — по числу подтверждённых качелей на флагнутых часах тестовых суток: **найдено на тех же сутках** (отбор по исходу; не прогноз). Допустимые пары с ≥ 1 качелей до t.")
    keys, orig = fi["keys"], fi["orig"]
    for iL, L in enumerate(LS):
        for iN, N in enumerate(NS):
            pr = fi["real"]["pred"][iL, iN, 3][:, ::2]
            yy = fi["real"]["y"][iL, iN, 0][:, ::2]
            hs = fi["real"]["hist"][iL, iN][:, ::2]
            sel = (pr >= so.FLAG_RATE) & (hs >= 1) & np.isfinite(yy) & fi["adm"][:, iN][:, None]
            tot = np.where(sel, yy, 0).sum(1)
            order = [i for i in np.argsort(-tot)[:30] if tot[i] > 0]
            p(f"**N = {N} %, L = {L} мс** — пар с флагом: {int(sel.any(1).sum())}, подтверждённых качелей всего: {int(tot.sum())}")
            if not order:
                p()
                continue
            p("| монета | пара | гр. | N/G | флагов (часов) | подтв. качелей | доля часов с ≥ 1 | валовый круг, % (мед.) | чистый 4 тейкера | чистый м+т (т) | чистый м+т (м) | глубина, $ (мед.) | глубина p10 |")
            p("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for i in order:
                k = keys[i]
                tc, dur, gr, side = D["pairs"][k]["sw"][(iN, L)]
                to = fi["orig"][::2]
                gs = []
                for o in np.flatnonzero(sel[i]):
                    a = to[o]
                    m = (tc > a) & (tc <= a + swings.HOUR)
                    gs += list(gr[m])
                gs = np.array(gs)
                dm, d10 = old.depth_med(D, k, iN, L) if hasattr(old, "depth_med") else (None, None)
                gm = np.median(gs) if len(gs) else np.nan
                p(f"| {k[0]} | {k[1]}→{k[2]} | {GN[int(fi['grp'][i])]} | {f(N / fi['G'][i], 1)} | {int(sel[i].sum())} | {int(tot[i])} | {f((yy[i][sel[i]] >= 1).mean())} | {f(gm, 3)} | {f(gm - FEE_TT, 3)} | {f(gm - FEE_MT, 3)} | {f(gm - FEE_MM, 3)} | {f(dm, 0)} | {f(d10, 0)} |")
            p()


def main(sw, dev, outdir, fin=None):
    global OUTDIR
    OUTDIR = outdir
    Path(outdir).mkdir(parents=True, exist_ok=True)
    D = pickle.load(open(sw, "rb"))
    dv = pickle.load(open(dev, "rb"))
    for q, k in enumerate(dv["keys"]):
        for iN in range(len(NS)):
            for iL, L in enumerate(LS):
                SW[(q, iN, iL)] = np.sort(D["pairs"][k]["sw"][(iN, L)][0].astype(np.int64))
    adm_table(dv)
    delay_table(dv)
    score_table(f"Разработка: первые {dv['Ts'] / 3.6e6:.1f} ч (разведка; гиперпараметры подобраны здесь)", dv["score"], dv, None)
    flag_table("Разработка: флаг «≥ 1 качели/ч»", dv["flags"])
    if fin:
        fi = pickle.load(open(fin, "rb"))
        p(f"Финал: преддекларация `{fi['prereg_sha']}`, скрипт sha256 `{fi['script_sha256'][:16]}…`, R = {fi['R'][0]} (пары) / {fi['R'][1]} (фаз.), {len(fi['orig'])} точек отсчёта с {time.strftime('%d.%m %H:%M', time.gmtime((fi['t0'] + fi['Ts']) / 1000))} UTC.")
        p()
        score_table(f"Финал: последние {(fi['Te'] - fi['Ts']) / 3.6e6:.1f} ч (тест)", fi["score"], dv, None)
        rows, th = verdict(fi)
        top30(D, fi, rows)
    Path(outdir, "report2.md").write_text("\n".join(OUT), encoding="utf-8")
    print("\n".join(OUT))


if __name__ == "__main__":
    main(*sys.argv[1:5])
