#!/usr/bin/env python3
"""TK-617 v2: замер «защиты до касания уровня» на сделках L_base (описательно, 01.07–13.11) → markdown. Вход — `pretouch.parquet` (`tk617_pretouch.py`).

Правило А(X): до касания уровня цена ушла на ≥ X бп против входа → выход по рынку (первая сделка ленты ≥ момент + 4 мс, проскальзывание 1 бп, тейкер).
Правило Б(k, X): за k мин от входа цена не касалась уровня и на контрольной точке ход ≤ −X бп → выход по рынку на контрольной точке.
Δ сделки = P&L по правилу − P&L движка (gross − fees); сделка вне правила — Δ = 0. Остальная логика движка (тейки, трейл, `rt`, `be`) не пересчитывается: до касания тейков
нет (первый тейк — выше уровня), так что для сработавших сделок Δ точна с точностью до тика ленты; портфельные связи (занятость монеты, паузы после стопов) не моделируются.
Это замер на старых данных (§13.29, «найдено на этих данных»), 36 клеток = счётчик испытаний; Холм — по ним.
"""
import argparse
import os

import numpy as np
import pandas as pd

NOTIONAL = 500.0
FEE = 2 * 3.15e-4 * NOTIONAL      # вход + выход, тейкер
SLIP = 1.0                        # бп, выход
XS = (25, 50, 75, 100, 150, 200, 300, 400)
KS = (1, 2, 3, 5, 10, 15, 30)
XB = (0, 50, 100, 200)
NDAYS, BLOCK, NB = 136, 4, 4000
MONTHS = ["jul", "aug", "sep", "oct", "nov"]
rng = np.random.default_rng(6173)


def cf_pnl(x_bp):
    return NOTIONAL * (x_bp - SLIP) / 1e4 - FEE


def boot(d, delta, n_all):
    """Среднее Δ на сделку по всем сделкам ряда: круговой блочный бутстреп по суткам; возвращает (lo, hi, p одностороннее P(Δ ≤ 0))."""
    sd = np.bincount(d, weights=delta, minlength=NDAYS)[:NDAYS]
    nd = np.bincount(d, minlength=NDAYS)[:NDAYS].astype(float)
    nbk = -(-NDAYS // BLOCK)
    st = rng.integers(0, NDAYS, size=(NB, nbk))
    ix = ((st[:, :, None] + np.arange(BLOCK)[None, None, :]) % NDAYS).reshape(NB, -1)[:, :NDAYS]
    S, N = sd[ix].sum(1), nd[ix].sum(1)
    m = S / np.where(N > 0, N, np.nan)
    m = m[~np.isnan(m)]
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5)), max(float((m <= 0).mean()), 1.0 / len(m))


def holm(ps):
    o = sorted(range(len(ps)), key=lambda i: ps[i])
    out, run = [np.nan] * len(ps), 0.0
    for r, i in enumerate(o):
        run = max(run, min(1.0, (len(ps) - r) * ps[i]))
        out[i] = run
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="/data/p42-tk617")
    ap.add_argument("--out", default="/data/p42-tk617/cf.md")
    a = ap.parse_args()
    P = pd.read_parquet(os.path.join(a.dir, "pretouch.parquet"))
    F = pd.read_parquet(os.path.join(a.dir, "trades_feat.parquet"), columns=["set", "win", "sym", "level", "sig_ms", "t_out", "month", "hold_s", "natr_bp", "mae_h"])
    P = P.merge(F, on=["set", "win", "sym", "level", "sig_ms", "t_out"], how="left")
    L = P[P["set"] == "L_base"].reset_index(drop=True)
    assert len(L) == 621 and L["month"].notna().all(), (len(L), L["month"].isna().sum())
    d = L["abs_day"].values.astype(int)
    stop = (L["kind"] == "stop").values
    o = []
    W = o.append
    cells = []   # (имя, флаг, x_выхода)
    for X in XS:
        fl = L["tc_%d" % X].notna().values
        cells.append(("A(%d)" % X, fl, L["xc_%d" % X].values))
    for k in KS:
        for X in XB:
            fl = ((L["tch_%d" % k] == 0) & (L["xk_%d" % k] <= -X) & (L["hold_s"] * 1000 > k * 60_000 + 4)).values
            cells.append(("Б(%d мин, %d бп)" % (k, X), fl, L["xk_%d" % k].values))
    rows, res = [], []
    for nm, fl, xe in cells:
        fl = fl & ~np.isnan(xe)
        delta = np.where(fl, cf_pnl(np.nan_to_num(xe)) - L["gf"].values, 0.0)
        lo, hi, p = boot(d, delta, len(L))
        res.append((nm, fl, delta, lo, hi, p))
    hp = holm([r[5] for r in res])
    for (nm, fl, delta, lo, hi, p), h in zip(res, hp):
        w = fl & ~stop
        rows.append([nm, int(fl.sum()), int((fl & stop).sum()), int(w.sum()), "%+.1f" % L["gf"].values[w].sum(), "%+.1f" % delta.sum(), "%+.3f [%+.3f; %+.3f]" % (delta.sum() / len(L), lo, hi),
                     "%.3f" % h] + ["%+.1f" % delta[(L["month"] == m).values].sum() for m in MONTHS])
    W("## Замер правил до касания на L_base (621 сделка; 37 стопов; Σ движка +7,06 $)\n")
    W("Правило А(X) — пре-касательный стоп на −X бп; Б(k, X) — контрольная точка k мин без касания и ход ≤ −X бп. Δ — Σ по сделкам, $; Δ/сделку — на все 621 сделок [95 %% блочный бутстреп по суткам]; "
      "p по Холму — односторонний P(Δ ≤ 0) по %d клеткам.\n" % len(cells))
    W("\n".join(["| " + " | ".join(["правило", "сработало", "из них стопов (37)", "из них не стопов", "Σ P&L движка у не-стопов, $", "ΣΔ, $", "Δ на сделку [95 %]", "p_Холм"] + ["Δ " + m for m in MONTHS]) + " |",
                 "|" + "---|" * 13] + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]))
    W("\n### Матрица Б: ΣΔ, $ (строки — k, столбцы — X бп)\n")
    hdr = "| k, мин | " + " | ".join("X=%d" % X for X in XB) + " |\n|" + "---|" * (len(XB) + 1)
    mat = []
    for k in KS:
        r = ["%d" % k]
        for X in XB:
            for (nm, fl, delta, *_ ) in res:
                if nm == "Б(%d мин, %d бп)" % (k, X):
                    r.append("%+.0f (%d/%d)" % (delta.sum(), int((fl & stop).sum()), int(fl.sum())))
        mat.append("| " + " | ".join(r) + " |")
    W(hdr + "\n" + "\n".join(mat) + "\n\nВ скобках: пойманные стопы / всего сработало.")
    W("\n### Что у не-стопов, сработавших в Б(5 мин, 100 бп) и А(100): реальный исход движка\n")
    for nm in ("A(100)", "Б(5 мин, 100 бп)"):
        fl = [r for r in res if r[0] == nm][0][1]
        w = L[fl & ~stop]
        W("**%s**: не-стопов %d; исходы: %s; Σ $ %+.1f; $/сделку %+.2f; MAE за удержание (бп, среднее) %.0f\n" % (nm, len(w), ", ".join("%s %d" % (k, v) for k, v in w["kind"].value_counts().items()),
                                                                                          w["gf"].sum(), w["gf"].mean() if len(w) else float("nan"), w["mae_h"].mean() if "mae_h" in w and len(w) else float("nan")))
    # потолок: все 37 стопов закрыты на −X бп
    W("\n### Потолок: если бы все 37 стопов закрывались в точке −X бп (без потерь у других сделок)\n")
    srows = []
    for X in (100, 200, 300, 500):
        srows.append([X, "%+.1f" % ((cf_pnl(-float(X)) - L["gf"].values[stop]).sum())])
    W("| X, бп | выигрыш, $ |\n|---|---|\n" + "\n".join("| %s | %s |" % tuple(r) for r in srows))
    open(a.out, "w", encoding="utf-8").write("\n".join(o) + "\n")
    print("written", a.out, len(o))


if __name__ == "__main__":
    main()
