"""Справочный (не из преддекларации) разброс разности Спирмена «предиктор − naive24» для тройки (группа, N, L) финала.

Запуск: python3 swings_oos2_ci.py <oos2_final.pkl> <группа 0|1> <индекс N> <индекс L> <выход.md> [B]
Интервалы 95 %: (а) блочный бутстреп по точкам отсчёта (блок 12 точек = 6 ч), (б) бутстреп по парам (одни и те же пары во всех точках). Для H = 24 ч одна точка — только (б).
"""
import pickle
import sys
from pathlib import Path

import numpy as np
from scipy.stats import rankdata

sys.path.insert(0, str(Path(__file__).parent))
import swings_oos2 as so  # noqa: E402


def rho(x, z):
    a, b = rankdata(x), rankdata(z)
    return np.corrcoef(a, b)[0, 1] if a.min() < a.max() and b.min() < b.max() else np.nan


def main(src, g, iN, iL, dst, B=1000):
    fi = pickle.load(open(src, "rb"))
    R = fi["real"]
    grp, adm = fi["grp"], fi["adm"]
    pop = (grp == g)[:, None] & adm[:, iN][:, None] & (R["hist"][iL, iN] >= 1)
    rng = np.random.default_rng(11)
    out = [f"Тройка: группа {('ok', 'bx')[g]}, N = {so.NS[iN]} %, L = {so.LS[iL]} мс. Δ = Спирмен(предиктор) − Спирмен(naive24), среднее по точкам теста; 95 % интервалы бутстрепом (B = {B}); справочно, не из преддекларации.", "",
           "| H, ч | предиктор | точек | пар | Δ | блочный по точкам | по парам | доля точек с Δ > 0 |", "|---|---|---|---|---|---|---|---|"]
    for ih, h in enumerate(so.HS):
        y = R["y"][iL, iN, ih]
        ok = pop & np.isfinite(y)
        origs = [o for o in range(y.shape[1]) if ok[:, o].sum() >= so.MIN_POP]
        if not origs:
            continue
        for ip in range(1, len(so.ALLP)):
            d = []
            for o in origs:
                m = ok[:, o]
                d.append(rho(R["pred"][iL, iN, ip][m, o], y[m, o]) - rho(R["pred"][iL, iN, 0][m, o], y[m, o]))
            d = np.array(d)
            # блочный по точкам
            if len(d) >= 12:
                nb = len(d)
                bm = []
                for _ in range(B):
                    idx = np.concatenate([np.arange(s, s + 12) % nb for s in rng.integers(0, nb, int(np.ceil(nb / 12)))])[:nb]
                    bm.append(d[idx].mean())
                ci_o = f"[{np.percentile(bm, 2.5):+.3f}; {np.percentile(bm, 97.5):+.3f}]"
            else:
                ci_o = "—"
            # по парам: общая выборка пар на все точки
            pairs = np.flatnonzero(ok[:, origs[0]])
            bp = []
            for _ in range(max(B // 5, 100)):
                pick = rng.choice(pairs, len(pairs))
                dd = []
                for o in origs:
                    m = ok[pick, o]
                    xs, zs, ns = R["pred"][iL, iN, ip][pick, o][m], y[pick, o][m], R["pred"][iL, iN, 0][pick, o][m]
                    dd.append(rho(xs, zs) - rho(ns, zs))
                bp.append(np.nanmean(dd))
            ci_p = f"[{np.percentile(bp, 2.5):+.3f}; {np.percentile(bp, 97.5):+.3f}]"
            out.append(f"| {h} | {so.ALLP[ip]} | {len(origs)} | {len(pairs)} | {np.nanmean(d):+.3f} | {ci_o} | {ci_p} | {np.mean(d > 0):.2f} |")
    Path(dst).write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5], int(sys.argv[6]) if len(sys.argv) > 6 else 1000)
