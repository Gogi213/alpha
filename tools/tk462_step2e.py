#!/usr/bin/env python3
"""TK-462 шаг 2e (P-TK462 §6 X3, часть X1): по выписке, без минуток. python tk462_step2e.py <entries.csv.gz> <out.csv>
X3: форма B (стоп), reason=stop: gross vs -risk (в симуляции стоп исполняется по уровню — гэп не моделируется, значит X3 = "не смоделировано"; доля стопов с gross<-risk показывает, считает ли кэш гэп).
Верхняя граница X3 через минутки НЕ считалась (нет уровня стопа/open в выписке)."""
import sys, numpy as np, pandas as pd
a = pd.read_csv(sys.argv[1], usecols=["sym", "form", "reason", "gross", "risk", "year"])
b = a[(a.form.astype(str).str.upper().str.startswith("B")) & (a.reason == "stop") & a.risk.notna()]
d = b.gross + b.risk
rows = [dict(n_B=int((a.form.astype(str).str.upper().str.startswith("B")).sum()), n_stop=len(b),
             share_stop_gap=float((d < -1e-6).mean()), share_stop_exact=float((d.abs() < 1e-6).mean()),
             share_stop_better=float((d > 1e-6).mean()), mean_gap_bps=float(d[d < -1e-6].mean()) if (d < -1e-6).any() else 0.0,
             gap_contrib_bps_per_B_trade=float(d[d < -1e-6].sum() / max(1, len(a[a.form.astype(str).str.upper().str.startswith("B")]))))]
r = pd.DataFrame(rows); r.to_csv(sys.argv[2], index=False); print(r.T)
print(a.reason.value_counts().head(8))
