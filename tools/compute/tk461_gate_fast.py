#!/usr/bin/env python3
"""Гейт TK-461 на реальных данных: scan-строки (28 ячеек) эталон S.trade vs trade_fast, побитно (to_csv). python tk461_gate_fast.py <csv.gz> <tf> <events> <fund> [N]"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk461_sim as S, tk461_scan as K
from tk461_fast import trade_fast
p, tf, ev, fc = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
n = int(sys.argv[5]) if len(sys.argv) > 5 else 40
ref = K.run(p, tf, ev, fc, n).to_csv(index=False)
S0 = S.trade
S.trade = trade_fast
new = K.run(p, tf, ev, fc, n).to_csv(index=False)
S.trade = S0
print(tf, "rows", ref.count("\n"), "IDENTICAL" if ref == new else "DIFF")
sys.exit(0 if ref == new else 1)
