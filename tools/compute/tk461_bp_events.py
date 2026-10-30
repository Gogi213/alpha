#!/usr/bin/env python3
"""TK-461 / P-TK461 §12.2 п.1: bp-события (B2) -> входной csv ядра TK-463 с ss_bps/ot1_bps. Исходов не считает.
python tk461_bp_events.py <g4|g41|g42> <events.csv.gz bp-gN> <каталог минут csv.gz> <out.csv.gz> [SYM ...]
Шаг 1 (30.10): семейство G4 (имбаланс, def.imbalance+fulfill:bull|bear): сторона = +1 бык / -1 медведь; событие ts = конец бара j
(bp_g4: (idx[j]+1)*tf*60000), вход по close бара j (= open первой минуты после ts, как Санчо), зона = [h[j-2], l[j]] (в ориентации
быка), дальний от входа край = h[j-2]; ss_bps = (c[j] - h[j-2])/|c[j]|*1e4 (для медведя зеркало). Колонки как у sig_table:
sym,tf,ev,dir,side,t0,tt0,atr_bps + ss_bps,ot1_bps,ot1_fallback. tt0 = время открытия бара j, atr_bps = ATR14(j)/|c[j]|*1e4."""
import importlib.util
import os
import sys
import numpy as np
import pandas as pd

_s = importlib.util.spec_from_file_location("bp_g4", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bp_g4.py"))
G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(G)


def g4_cols(ev, sym, name, tf, idx, h, l, c):
    """ev: строки одной монеты и ТФ (ts_utc_ms, def_id). -> DataFrame событий с ss_bps."""
    atr = G.atr14(h, l, c)
    j = (ev.ts_utc_ms.values // (tf * 60000) - 1 - idx[0]).astype(int)
    sign = np.where(ev.def_id.str.endswith(":bull"), 1, -1)
    cj, atrj = c[j], atr[j]
    far = np.where(sign > 0, h[j - 2], l[j - 2])           # дальний от входа край зоны: бык - h[j-2], медведь - l[j-2]
    ss = sign * (cj - far) / np.abs(cj) * 1e4
    ss = np.where(np.isfinite(ss) & (ss > 0), ss, np.nan)
    out = pd.DataFrame(dict(sym=sym, tf=tf, ev="g4", dir=np.where(sign > 0, "bull", "bear"), side=sign, t0=j + 1,
                            tt0=idx[j] * tf * 60000, atr_bps=atrj / np.abs(cj) * 1e4, ss_bps=ss))
    out["ot1_bps"] = out.ss_bps
    out["ot1_fallback"] = 0
    return out


if __name__ == "__main__":
    kind, evp, src, outp = sys.argv[1:5]
    syms = sys.argv[5:]
    ev = pd.read_csv(evp)
    if syms:
        ev = ev[ev.coin.isin(syms)]
    parts = []
    for sym, e in ev.groupby("coin"):
        for name, tf in G.TFS.items():
            x = e[e.tf == name]
            if len(x):
                idx, h, l, c, ok = G.bars(f"{src}/{sym}.csv.gz", tf)
                parts.append(g4_cols(x, sym, name, tf, idx, h, l, c))
    out = pd.concat(parts) if parts else pd.DataFrame()
    out.to_csv(outp, index=False, compression="gzip")
    print(outp, len(out), int(out.ss_bps.notna().sum()) if len(out) else 0)
