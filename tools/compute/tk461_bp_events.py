#!/usr/bin/env python3
"""TK-461 / P-TK461 §12.2 п.1: bp-события (B2) -> входной csv ядра TK-463 с ss_bps/ot1_bps. Исходов не считает.
python tk461_bp_events.py <g1|g4|g41|g42> <events.csv.gz bp-gN> <каталог минут csv.gz> <out.csv.gz> [SYM ...]
Шаг 1 (30.10): семейство G4 (имбаланс, def.imbalance+fulfill:bull|bear): сторона = +1 бык / -1 медведь; событие ts = конец бара j
(bp_g4: (idx[j]+1)*tf*60000), вход по close бара j (= open первой минуты после ts, как Санчо), зона = [h[j-2], l[j]] (в ориентации
быка), дальний от входа край = h[j-2]; ss_bps = (c[j] - h[j-2])/|c[j]|*1e4 (для медведя зеркало). Колонки как у sig_table:
sym,tf,ev,dir,side,t0,tt0,atr_bps + ss_bps,ot1_bps,ot1_fallback. tt0 = время открытия бара j, atr_bps = ATR14(j)/|c[j]|*1e4.
G41 (закрытие телом за имбалансом, P-TK458-b2-g41): событие на баре k (первый бар j+1..j+10 с low<=h[j-2], close<h[j-2]; ts=(idx[k]+1)*tf*60000),
сторона = ПРОТИВ имбаланса (бычий имбаланс, закрытие под ним -> -1 шорт: протокол «не отработан» = нет возврата в щель, т.е. движение продолжается
от щели), вход = close k, стоп = возврат в щель (ближняя граница l[j], как исход протокола): ss_bps = (l[j]-c[k])/|c[k]|*1e4 в зеркальной рамке.
G42 (P-TK458-b2-g42): зеркало D1-строк G41 - те же события, сторона инвертирована (возврат в щель); стоп протоколом не задан -> ss_bps/ot1_bps пустые
(ядро пропускает SS/ОТ для строки, остальные ячейки считаются от ATR/входа); независимого свидетельства не добавляет (протокол: вердикт симметричен G41).
Строка события сопоставляется со строкой bp-g41-events по (ts,coin,tf,def_id) и g_atr (g/ATR14[j]); без пары - не берётся."""
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


def g1_cols(ev, sym, name, tf, idx, h, l, c):
    """G1.1 (P-TK458-b2-g1): событие на баре подтверждения свинга t=b+2 (ts=(idx[t]+1)*tf*60000), вход = close t, сторона = ожидаемый откат
    (после свинг-хая шорт -1, после свинг-лоя лонг +1), стоп = обновление конца ноги: ss_bps = (H[b]-C[t])/|C[t]|*1e4 (лой - зеркало)."""
    atr = G.atr14(h, l, c)
    t = (ev.ts_utc_ms.values // (tf * 60000) - 1 - idx[0]).astype(int)
    hi = ev.def_id.str.endswith(":high").values
    cj = c[t]
    ss = np.where(hi, h[t - 2] - cj, cj - l[t - 2]) / np.abs(cj) * 1e4
    ss = np.where(np.isfinite(ss) & (ss > 0), ss, np.nan)
    out = pd.DataFrame(dict(sym=sym, tf=name, ev="g1", dir=np.where(hi, "high", "low"), side=np.where(hi, -1, 1), t0=t + 1,
                            tt0=idx[t] * tf * 60000, atr_bps=atr[t] / np.abs(cj) * 1e4, ss_bps=ss))
    out["ot1_bps"] = out.ss_bps
    out["ot1_fallback"] = 0
    return out


def g41_cols(ev, sym, name, tf, idx, h, l, c, ok):
    """ev: строки bp-g41-events одной монеты и ТФ. Пересчёт (k,j) тем же правилом, что bp_g41.side; пары - по g_atr."""
    atr = G.atr14(h, l, c)
    n = len(h)
    rows = []
    for dn, sign in (("bull", 1), ("bear", -1)):
        x = ev[ev.def_id.str.endswith(":" + dn)]
        if not len(x):
            continue
        hh, ll, cc = (h, l, c) if sign > 0 else (-l, -h, -c)
        gap = np.zeros(n, bool)
        gap[2:] = ok[2:] & ok[1:-1] & ok[:-2] & (ll[2:] > hh[:-2])
        for ts, ga in zip(x.ts_utc_ms.values, x.g_atr.values):
            k = int(ts // (tf * 60000) - 1 - idx[0])
            for j in range(max(k - 10, 2), k):
                if not gap[j] or j + 11 > n or np.isnan(atr[j]) or atr[j] <= 0:
                    continue
                top = hh[j - 2]
                hit = np.flatnonzero(ll[j + 1:j + 11] <= top)
                if len(hit) == 0 or j + 1 + hit[0] != k or not cc[k] < top:
                    continue
                if abs((ll[j] - hh[j - 2]) / atr[j] - ga) > 1e-9:
                    continue
                D = (ll[j] - cc[k]) / abs(c[k]) * 1e4
                rows.append(dict(sym=sym, tf=name, ev="g41", dir=dn, side=-sign, t0=k + 1, tt0=idx[k] * tf * 60000,
                                 atr_bps=atr[k] / abs(c[k]) * 1e4, ss_bps=D if D > 0 else np.nan))
                break
    out = pd.DataFrame(rows)
    if len(out):
        out["ot1_bps"] = out.ss_bps
        out["ot1_fallback"] = 0
    return out


def g42_cols(ev, sym, name, tf, idx, h, l, c, ok):
    if name != "D1":
        return pd.DataFrame()
    out = g41_cols(ev, sym, name, tf, idx, h, l, c, ok)
    if len(out):
        out["ev"] = "g42"
        out["side"] = -out.side
        out["ss_bps"] = np.nan
        out["ot1_bps"] = np.nan
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
                parts.append(g4_cols(x, sym, name, tf, idx, h, l, c) if kind == "g4" else
                             g1_cols(x, sym, name, tf, idx, h, l, c) if kind == "g1" else
                             g42_cols(x, sym, name, tf, idx, h, l, c, ok) if kind == "g42" else
                             g41_cols(x, sym, name, tf, idx, h, l, c, ok))
    out = pd.concat(parts) if parts else pd.DataFrame()
    out.to_csv(outp, index=False, compression="gzip")
    print(outp, len(out), int(out.ss_bps.notna().sum()) if len(out) else 0)
