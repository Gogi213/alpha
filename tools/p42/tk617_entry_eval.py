#!/usr/bin/env python3
"""TK-617 v2: итоги повтора по ленте `entry_replay.parquet` (`tk617_entry_replay.py`) → markdown: сверка k = 0 с движком и замер входа по подтверждению E(k)."""
import argparse
import os

import numpy as np
import pandas as pd

KS = (0, 5, 10, 15, 20, 25)
CODES = {0: "stop", 1: "be", 2: "trail", 3: "rt", 4: "forced"}
MONTHS = ["jul", "aug", "sep", "oct", "nov"]
NDAYS, BLOCK, NB = 136, 4, 4000
rng = np.random.default_rng(6174)


def boot(d, delta):
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
    ap.add_argument("--out", default="/data/p42-tk617/entry.md")
    a = ap.parse_args()
    R = pd.read_parquet(os.path.join(a.dir, "entry_replay.parquet"))
    F = pd.read_parquet(os.path.join(a.dir, "trades_feat.parquet"), columns=["set", "win", "sym", "level", "sig_ms", "t_out", "month", "dist_bp", "natr_bp"])
    R = R.merge(F, on=["set", "win", "sym", "level", "sig_ms", "t_out"], how="left")
    assert len(R) == 621 and R["month"].notna().all()
    d = R["abs_day"].values.astype(int)
    o, W = [], None
    W = o.append
    # --- сверка k = 0 -------------------------------------------------------------------------------------------------------
    ok0 = R["code_0"] >= 0
    pnl0 = (R["gross_0"] - R["fees_0"]).where(ok0)
    diff = (pnl0 - R["gf"]).abs()
    match = ok0 & (diff < 0.01) & (R["code_0"].map(CODES) == R["kind"])
    W("## 1. Сверка повтора (k = 0) с движком на всех 621 сделках L_base\n")
    W("Совпало (вид выхода и P&L ≤ 0,01 $): **%d из 621**; повтор не вошёл: %d; расхождение P&L > 0,01 $: %d; другой вид выхода: %d. Σ повтора %+.2f $ против Σ движка %+.2f $.\n" % (
        match.sum(), (~ok0).sum(), (ok0 & (diff >= 0.01)).sum(), (ok0 & (R["code_0"].map(CODES) != R["kind"])).sum(), pnl0.sum(), R["gf"].sum()))
    bad = R[ok0 & ~match]
    if len(bad):
        W("Расхождения (до 15): " + "; ".join("%s %s движок %s %+.2f / повтор %s %+.2f" % (r.sym, r.month, r.kind, r.gf, CODES.get(r.code_0), r.gross_0 - r.fees_0) for r in bad.head(15).itertuples()) + "\n")
    # --- E(k) ---------------------------------------------------------------------------------------------------------------
    W("\n## 2. Вход по подтверждению E(k): рыночная покупка, когда цена поднялась на k бп над ценой входа базы (приказ живёт 60 мин)\n")
    W("Δ = Σ P&L повтора по всем 621 сигналам (не вошедшие — 0) − Σ P&L движка; на сделку — на все 621 [95 %% блочный бутстреп по суткам]; p по Холму — односторонний P(Δ ≤ 0) по %d клеткам. "
      "Единичные сделки, портфель не моделируется.\n" % (len(KS) - 1))
    rows, res = [], []
    base = R["gf"].values
    for k in KS:
        filled = (R["code_%d" % k] >= 0).values
        pnl = np.where(filled, (R["gross_%d" % k] - R["fees_%d" % k]).values, 0.0)
        delta = pnl - base
        kinds = pd.Series(R["code_%d" % k].map(CODES).where(filled)).value_counts()
        res.append((k, filled, pnl, delta, kinds))
    ps = []
    for k, filled, pnl, delta, kinds in res:
        if k == 0:
            ps.append(None)
            continue
        lo, hi, p = boot(d, delta)
        ps.append((lo, hi, p))
    hp = holm([x[2] for x in ps if x is not None])
    hi_ = iter(hp)
    for (k, filled, pnl, delta, kinds), pp in zip(res, ps):
        n, ns = int(filled.sum()), int(kinds.get("stop", 0))
        ci = "%+.3f [%+.3f; %+.3f]" % (delta.sum() / 621, pp[0], pp[1]) if pp else "—"
        rows.append(["E(%d)" % k if k else "база (k = 0)", n, ns, "%+.1f" % pnl.sum(), "%+.2f" % (pnl.sum() / n if n else float("nan")), "%+.1f" % delta.sum(), ci,
                     "%.3f" % next(hi_) if pp else "—"] + ["%+.1f / %d" % (pnl[(R["month"] == m).values].sum(), int(filled[(R["month"] == m).values].sum())) for m in MONTHS])
    W("| вход | вошло | стопов | Σ P&L, $ | $ на сделку | Δ, $ | Δ на сигнал [95 %] | p_Холм | " + " | ".join(MONTHS) + " |\n|" + "---|" * (8 + len(MONTHS)))
    W("\n".join("| " + " | ".join(str(c) for c in r) + " |" for r in rows))
    W("\nМесяцы: Σ P&L / число вошедших сделок.\n")
    # --- что отсеялось ------------------------------------------------------------------------------------------------------
    W("\n## 3. Кого отсеивает E(k): сделки базы, у которых приказ не исполнен или цена ≥ уровня\n")
    rows = []
    for k, filled, pnl, delta, kinds in res[1:]:
        miss = ~filled
        code = R["code_%d" % k]
        rows.append(["E(%d)" % k, int(miss.sum()), int((miss & (R["kind"] == "stop")).sum()), "%+.1f" % R["gf"].values[miss & (R["kind"] == "stop").values].sum(), int((miss & (R["kind"] != "stop")).sum()),
                     "%+.1f" % R["gf"].values[miss & (R["kind"] != "stop").values].sum(), int((code == -2).sum()), int((code == -3).sum()), int((code == -1).sum())])
    W("| вход | не вошло | из них стопов базы | Σ базы у них, $ | из них не стопов | Σ базы у них, $ | цена ≥ уровня | приказ не исполнен | нет сделки покупателя |\n|" + "---|" * 9)
    W("\n".join("| " + " | ".join(str(c) for c in r) + " |" for r in rows))
    # --- виды выходов -------------------------------------------------------------------------------------------------------
    W("\n## 4. Виды выходов у вошедших\n")
    W("| вход | " + " | ".join(CODES.values()) + " |\n|" + "---|" * (1 + len(CODES)))
    for k, filled, pnl, delta, kinds in res:
        W("| %s | " % ("E(%d)" % k if k else "база") + " | ".join("%d / %+.1f" % (int(kinds.get(nm, 0)), pnl[(R["code_%d" % k].map(CODES) == nm).values].sum()) for nm in CODES.values()) + " |")
    open(a.out, "w", encoding="utf-8").write("\n".join(o) + "\n")
    print("written", a.out)


if __name__ == "__main__":
    main()
