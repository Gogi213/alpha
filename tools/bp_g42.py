"""TK-458 B2 G4.2: bp08-04 «закрепление за границей D1-имбаланса → откат/fulfill» — D1-строки событий G4.1 с исходом «возврат в щель» (= 1 − «не отработан»).
Протокол docs/research/P-TK458-b2-g42-2026-10-30.md.
python -I tools/bp_g42.py <bp-g41-events.csv.gz> <каталог-вывода-events> ; затем bp_g4_agg.py <каталог> <префикс>"""
import sys, os
import pandas as pd
d = pd.read_csv(sys.argv[1]); d = d[d.tf == "D1"].copy()
for c in ["f_ful", "f_50", "c_ful", "c_50"]: d[c] = 1 - d[c]
d["def_id"] = d.def_id.str.replace("def.imbalance+close", "def.imbalance+close.fulfill", regex=False)
os.makedirs(sys.argv[2], exist_ok=True); d.to_csv(sys.argv[2] + "/g42-events.csv.gz", index=False)
