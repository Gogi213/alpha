#!/usr/bin/env python3
"""TK-461 D2b S1-S5: bpev/out/s-{1..5}.csv.gz (события + флаги, исходов нет) -> <out_dir>/<SYM>-<tf>.csv.gz в формате ядра.
Подмножество «с фактором» = отдельное имя ev (ядро флагов не читает); события те же, что в TK-451 (A5) / sm_s3 (S3), строки не меняются.
S1: asia|frankfurt|london|ny ==1 (1H); S2: prem_a|prem_b ==1 (премиум) и ==0 (дискаунт), NaN исключены; S4: pD1|pW1 ==1;
S5: s5==1 и s5x==0; S3: a8==1 по якорю k (исход p459 в фильтр НЕ входит: он известен после события).
python tk461_s_stage.py <out_dir> <bpev/out каталог>"""
import os
import sys
import pandas as pd

out, src = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
COLS = ["sym", "tf", "ev", "dir", "side", "t0", "tt0", "atr_bps", "ss_bps", "ot1_bps", "ot1_fallback"]
parts = []


def add(d, mask, name):
    parts.append(d[mask].assign(ev=name)[COLS])


d = pd.read_csv(f"{src}/s-1.csv.gz")
for f in ("asia", "frankfurt", "london", "ny"):
    add(d, d[f] == 1, f"S1-{f}")
d = pd.read_csv(f"{src}/s-2.csv.gz")
for f, n in (("prem_a", "pa"), ("prem_b", "pb")):
    add(d, d[f] == 1, f"S2-{n}-prem")
    add(d, d[f] == 0, f"S2-{n}-disc")
d = pd.read_csv(f"{src}/s-4.csv.gz")
for f in ("pD1", "pW1"):
    add(d, d[f] == 1, f"S4-{f}")
d = pd.read_csv(f"{src}/s-5.csv.gz")
add(d, (d.s5 == 1) & (d.s5x == 0), "S5")
d = pd.read_csv(f"{src}/s-3.csv.gz")
for k in sorted(d.anchor.unique()):
    add(d, (d.a8 == 1) & (d.anchor == k), f"S3-k{k}")
al = pd.concat(parts, ignore_index=True)
al = al[al.atr_bps.notna() & (al.atr_bps > 0)]
for (sym, tf), g in al.groupby(["sym", "tf"]):
    g.sort_values("tt0", kind="stable").to_csv(os.path.join(out, f"{sym}-{int(tf)}.csv.gz"), index=False)
print(al.groupby(["tf", "ev"]).size().to_string())
