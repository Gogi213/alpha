"""Ступень на границе суток 20:00 UTC (00:00 GMT+4) и профиль числа качелей по часам суток (замечание проверяющего 6).

Запуск: python3 swings_hod.py <swings2.pkl> <выход.md>
Часы — календарные UTC; в окно целиком входят 55 часов (13.11 07:00 → 15.11 14:00). Качели — L = 300 мс, по времени закрытия, пул по парам группы.
Статистика границы h: log((C_h + 0,5) / (C_{h−1} + 0,5)), C — число качелей группы в часе. Ранг 20:00 — по |статистике| среди всех календарных границ окна (54)
и среди 24 границ часа суток (среднее по повторам); по парам — знаки (рост / спад) среди пар с ≥ 3 качелей в двух часах отдельно по каждому повтору, биномиальный тест.
"""
import pickle
import sys

import numpy as np
from scipy.stats import binomtest

HOUR = 3_600_000
NS = (0.1, 0.2, 0.3, 0.5, 1.0)


def main(src, dst):
    d = pickle.load(open(src, "rb"))
    t0, t1 = d["t0"], d["t1"]
    h0 = -(-t0 // HOUR) * HOUR
    nb = int((t1 // HOUR * HOUR - h0) // HOUR)
    hod = (h0 // HOUR + np.arange(nb)) % 24
    keys = sorted(d["pairs"])
    grp = np.array([1 if "bingx" in k[1:] else 0 for k in keys])
    out = [f"Календарные часы UTC: {nb} полных (с {np.datetime64(int(h0), 'ms')} по {np.datetime64(int(h0 + nb * HOUR), 'ms')}); граница 20:00 UTC повторяется дважды (13.11 и 14.11).", ""]
    j20 = [j for j in range(1, nb) if hod[j] == 20]
    out += ["| группа | N, % | качелей за окно | 19:00–20:00 → 20:00–21:00, 13.11 | то же, 14.11 | log-отношение 13.11 / 14.11 | ранг среди 54 границ окна (по \\|log\\|, 1 = наибольшая) | ранг среди 24 границ часа суток (среднее по повторам) | знак по парам, 13.11; 14.11: рост / спад (p) |", "|---|---|---|---|---|---|---|---|---|"]
    prof = {}
    for g in (0, 1):
        for iN, N in enumerate(NS):
            C = np.zeros((int((grp == g).sum()), nb))
            for q, k in enumerate(np.flatnonzero(grp == g)):
                tc = t0 + d["pairs"][keys[k]]["sw"][(iN, 300)][0].astype(np.int64)
                idx = (tc - h0) // HOUR
                m = (idx >= 0) & (idx < nb)
                C[q] = np.bincount(idx[m].astype(int), minlength=nb)[:nb]
            tot = C.sum(0)
            if tot.sum() < 50:
                continue
            r = np.log((tot[1:] + 0.5) / (tot[:-1] + 0.5))                # r[j − 1] — граница в час j
            absr = np.abs(r)
            rank54 = lambda j: int((absr > absr[j - 1]).sum()) + 1        # noqa: E731
            byh = np.array([np.mean([r[j - 1] for j in range(1, nb) if hod[j] == h]) for h in range(24) if any(hod[j] == h for j in range(1, nb))])
            hs = [h for h in range(24) if any(hod[j] == h for j in range(1, nb))]
            ab = np.abs(byh)
            rank24 = int((ab > ab[hs.index(20)]).sum()) + 1
            sg = []
            for j in j20:
                m = (C[:, j] + C[:, j - 1]) >= 3
                up, dn = int((C[m, j] > C[m, j - 1]).sum()), int((C[m, j] < C[m, j - 1]).sum())
                sg.append((up, dn, binomtest(up, up + dn, 0.5).pvalue if up + dn else np.nan))
            a, b = j20
            out.append(f"| {('ok', 'bx')[g]} | {N} | {int(tot.sum())} | {int(tot[a - 1])} → {int(tot[a])} | {int(tot[b - 1])} → {int(tot[b])} | {r[a - 1]:+.2f} / {r[b - 1]:+.2f} | {rank54(a)} / {rank54(b)} | {rank24} из {len(hs)} | {sg[0][0]} / {sg[0][1]} ({sg[0][2]:.3f}); {sg[1][0]} / {sg[1][1]} ({sg[1][2]:.3f}) |")
            if N == 0.2:
                prof[g] = np.array([tot[hod == h].mean() for h in range(24)])
    out += ["", "Профиль: среднее число качелей группы за час по часам суток UTC (N = 0,2 %, L = 300 мс; часы 0–6 и 7–23 встречаются 2–3 раза):", "",
            "| группа | " + " | ".join(f"{h:02d}" for h in range(24)) + " |", "|---|" + "---|" * 24]
    for g in prof:
        out.append(f"| {('ok', 'bx')[g]} | " + " | ".join(f"{x:.0f}" for x in prof[g]) + " |")
    open(dst, "w", encoding="utf-8").write("\n".join(out))
    print("\n".join(out))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
