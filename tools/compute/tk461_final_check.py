#!/usr/bin/env python3
"""TK-461 возврат Судьи п.2: шапка и лучшие 10 главного отчёта по правилу P-TK461 §8 -- все 21288 вариантов несут БУ (be_r), поэтому
вердикт по всем -- на части «проверка» (нечётные монеты x 2024+, sancho-bp-exits-check-<дата>.csv); весь период -- описательно.
python tk461_final_check.py <findings-dir> <YYYY-MM-DD>  -> печатает markdown; пишет sancho-bp-exits-<дата>.csv с колонками проверки"""
import sys
import pandas as pd

d, dt = sys.argv[1:3]
K = ["family", "tf", "ev", "dir", "side", "stop", "take", "par", "be_r", "ot", "otp"]
rd = lambda f: pd.read_csv(f, dtype={"par": str, "be_r": str, "ot": str, "otp": str})
m = rd(f"{d}/sancho-bp-exits-{dt}.csv")
c = rd(f"{d}/sancho-bp-exits-check-{dt}.csv")
if "n_chk" not in m.columns:
    x = c[K + ["n", "syms", "base", "ctrl", "delta", "lo", "hi", "p_holm", "ymin", "delta_xa", "delta_xb"]].copy()
    x.columns = K + ["n_chk", "syms_chk", "base_chk", "ctrl_chk", "delta_chk", "lo_chk", "hi_chk", "p_holm_chk", "ymin_chk", "delta_xa", "delta_xb"]
    m = m.rename(columns={k: k + "_all" for k in ["n", "syms", "days", "base", "ctrl", "delta", "lo", "hi", "p", "p_holm", "ymin", "delta_по_годам", "small", "verdict"]})
    m = m.merge(x, on=K, how="left")
    assert m.n_chk.notna().all()
    m["h_pos"] = (m.p_holm_chk < 0.05) & (m.delta_chk > 0)
    m["h_neg"] = (m.p_holm_chk < 0.05) & (m.delta_chk < 0)
    m["h_pos_yr"] = m.h_pos & (m.ymin_chk > 0)
    m["h_pos_yr_base"] = m.h_pos_yr & (m.base_chk > 0)
    m["verdict"] = "не различимо"
    m.loc[m.h_pos_yr, "verdict"] = "лучше C-X (Холм, все годы; проверка)"
    m.loc[m.h_pos_yr_base, "verdict"] = "лучше C-X (Холм, все годы), net>0; проверка"
    m.loc[m.h_neg, "verdict"] = "хуже C-X (Холм; проверка)"
    m.to_csv(f"{d}/sancho-bp-exits-{dt}.csv", index=False, float_format="%.4f")
m["core"] = m.family + "|" + m.tf.astype(str) + "|" + m.ev + "|" + m["dir"].astype(str) + "|" + m.side.astype(str) + "|" + m.stop + "|" + m["take"] + "|" + m.par + "|" + m.be_r
g = m.groupby("family")
t = pd.DataFrame({"вариантов": g.size(), "net>0": g.apply(lambda x: (x.base_chk > 0).sum()), "Δ>0 (точка)": g.apply(lambda x: (x.delta_chk > 0).sum()),
                  "Холм Δ>0": g.h_pos.sum(), "Холм Δ<0": g.h_neg.sum(), "Холм Δ>0 + все годы": g.h_pos_yr.sum(), "…и net>0": g.h_pos_yr_base.sum(),
                  "…и xa,xb Δ>0": g.apply(lambda x: (x.h_pos_yr & (x.delta_xa > 0) & (x.delta_xb > 0)).sum()),
                  "независимых (без ОТ)": g.core.nunique(), "…из них с net>0 и Холм": g.apply(lambda x: x[x.h_pos_yr_base].core.nunique())})
t.loc["всего"] = [len(m), (m.base_chk > 0).sum(), (m.delta_chk > 0).sum(), m.h_pos.sum(), m.h_neg.sum(), m.h_pos_yr.sum(), m.h_pos_yr_base.sum(),
                  (m.h_pos_yr & (m.delta_xa > 0) & (m.delta_xb > 0)).sum(), m.core.nunique(), m[m.h_pos_yr_base].core.nunique()]
print(t.astype(int).to_markdown())
top = m[m.h_pos_yr_base].sort_values("base_chk", ascending=False).drop_duplicates("core").head(10)
tc = top[["family", "tf", "ev", "dir", "side", "stop", "take", "par", "be_r", "ot", "otp", "n_chk", "syms_chk", "base_chk", "ctrl_chk", "delta_chk", "lo_chk", "hi_chk", "ymin_chk", "delta_xa", "delta_xb", "base_all", "delta_all"]].round(2)
tc.insert(0, "#", range(1, len(tc) + 1))
print(tc.to_markdown(index=False))
print("all-period h_pos_yr_base in check-pass:", (m.h_pos_yr_base & (m.verdict_all.str.contains("net>0"))).sum(), "| all-period net>0&holm:", m.verdict_all.str.contains("net>0").sum())
