#!/usr/bin/env python3
"""TK-465 (хвост TK-461, P-TK461 §12.2 п.1): bp-события G5/G6/G61/G63/G7/G8 (B2) -> входной csv ядра TK-463. Исходов не считает.
python tk461_bp_events_b.py <g5|g6|g61|g63|g7|g8> <events.csv.gz bp-gN> <каталог минут csv.gz> <out.csv.gz> [SYM ...]
Колонки как у tk461_bp_events.py: sym,tf,ev,dir,side,t0,tt0,atr_bps,ss_bps,ot1_bps,ot1_fallback (+ tf_min - минут в баре tf для входа:
вход = open первой минуты >= tt0 + tf_min*60000). Набор событий = строки bp-gN-events (ключ ts,coin[,def_id]); код детекторов - модули
tools/bp_g{5,61,7,8}.py без правок (импорт). Правила, где протокол молчит (пусто = «не определено», ячейки SS/ОТ1 строки пропускаются, §12.2):
- сторона/стоп - только из P-TK458-b2-g*: G8 - сторона и ρ протокола (ss=ρ/E), G7 - сторона = ожидаемый разворот (хай-свип -> шорт),
  стоп протоколом не задан -> ss пусто; G5/G61/G6 - строки входов «bp-side-x2» (оба направления, dir=long|short), стоп не задан -> ss пусто.
- G5/G61: событие = первая минута снятия границы Азии, вход = open следующей минуты (tt0 = открытие минуты снятия, tf 1 мин); ATR14 минутный.
- G6: событие = окно (асия/франкфурт/лондон/ny), вход = open первой минуты окна (tt0 = старт окна - 1 ч, tf_min=60), ATR14 H1 бара до окна;
  ev=g6-<окно>, два направления.
- G7: событие = бар снятия s свинг-хая/лоя H1 (первый бар t+1..t+M с пробоем), вход = close s (tt0 = открытие s); ATR14 H1 на s.
- G8: бар i M5 (слом), вход = close i, side/ρ по протоколу; ATR14(M5) на i.
- G63: имбаланс Лондона (bull/bear M5, строки g63-out) как G4 (g4_cols), ev=g63; g63-freq (частота, сделок нет) в D2b не входит."""
import importlib.util
import os
import sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..")


def _load(name, path):
    s = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


G5 = _load("bp_g5", os.path.join(TOOLS, "bp_g5.py"))
G8 = _load("bp_g8", os.path.join(TOOLS, "bp_g8.py"))
G7 = _load("bp_g7", os.path.join(TOOLS, "bp_g7.py"))
G4 = G7.G  # bp_g4
E1 = _load("tk461_bp_events", os.path.join(HERE, "tk461_bp_events.py"))
COLS = ["sym", "tf", "ev", "dir", "side", "t0", "tt0", "atr_bps", "ss_bps", "ot1_bps", "ot1_fallback", "tf_min"]
R, T = G5.R, G5.T


def mk(sym, tf, tf_min, ev, dr, side, t0, tt0, atr_bps, ss=np.nan):
    n = len(t0)
    return pd.DataFrame(dict(sym=sym, tf=tf, ev=ev, dir=dr, side=side, t0=t0, tt0=tt0, atr_bps=atr_bps, ss_bps=ss,
                             ot1_bps=ss, ot1_fallback=0, tf_min=tf_min), index=range(n))


def load_min(path):
    d = pd.read_csv(path, usecols=[0, 1, 2, 3, 4])
    d.columns = ["t", "o", "h", "l", "c"]
    m = d.t.values // 60000
    m0 = int(m.min())
    n = int(m.max()) - m0 + 1
    a = {k: np.full(n, np.nan) for k in "ohlc"}
    for k in "ohlc":
        a[k][m - m0] = d[k].values
    return m0, a["h"], a["l"], a["c"]


def sweep_min(h, l, s):
    """как bp_g5.one: s - начало диапазона. -> None | (i минуты снятия от конца диапазона, +1 снят хай / -1 снят лой)"""
    if s < 0 or s + R + T > len(h):
        return None
    H, L = h[s:s + R + T], l[s:s + R + T]
    if np.isnan(H).any():
        return None
    ah, al = H[:R].max(), L[:R].min()
    if ah - al <= 0:
        return None
    up, dn = H[R:] > ah, L[R:] < al
    hit = np.flatnonzero(up | dn)
    if len(hit) == 0 or (up[hit[0]] and dn[hit[0]]):
        return None
    return int(hit[0]), (1 if up[hit[0]] else -1)


def asia_cols(ev, sym, path, name):
    """G5 / G61: по строке события - первая минута снятия; оба направления (long|short), стоп не задан."""
    m0, h, l, c = load_min(path)
    atr = G4.atr14(h, l, c)
    rows = []
    for ts in ev.ts_utc_ms.values:
        s0 = int(ts // 60000) - m0
        r = sweep_min(h, l, s0)
        if r is None:
            continue
        j = s0 + R + r[0]
        rows.append((j, m0 + j))
    if not rows:
        return pd.DataFrame(columns=COLS), 0
    j = np.array([x[0] for x in rows], dtype=np.int64)
    gm = np.array([x[1] for x in rows], dtype=np.int64)
    parts = []
    for dr, side in (("long", 1), ("short", -1)):
        parts.append(mk(sym, "1m", 1, name, dr, side, j + 1, gm * 60000, atr[j] / np.abs(c[j]) * 1e4))
    return pd.concat(parts), len(rows)


def g6_cols(ev, sym, path):
    idx, h, l, c, ok = G4.bars(path, 60)
    atr = G4.atr14(h, l, c)
    parts = []
    for w, x in ev.groupby(ev.def_id.str.split(":").str[1]):
        p = (x.ts_utc_ms.values // 3600000 - 1 - idx[0]).astype(int)
        good = (p >= 0) & (p < len(atr))
        a = np.full(len(p), np.nan)
        a[good] = atr[p[good]] / np.abs(c[p[good]]) * 1e4
        for dr, side in (("long", 1), ("short", -1)):
            parts.append(mk(sym, "1h*L", 60, "g6-" + w, dr, side, p + 1, x.ts_utc_ms.values.astype(np.int64) - 3600000, a))
    return pd.concat(parts) if parts else pd.DataFrame(columns=COLS), len(ev)


def g7_cols(ev, sym, path):
    idx, h, l, c, ok = G4.bars(path, 60)
    parts = []
    nev = 0
    for dn, sign in (("high", 1), ("low", -1)):
        x = ev[ev.def_id.str.endswith(":" + dn)]
        if not len(x):
            continue
        hh, ll, cc = (h, l, c) if sign > 0 else (-l, -h, -c)
        atr = G4.atr14(hh, ll, cc)
        t = (x.ts_utc_ms.values // 3600000 - 1 - idx[0]).astype(int)
        S = []
        for tt in t:
            s, _ = G7.sweep_s(hh, cc, tt + 1, hh[tt - 1])
            S.append(s)
        S = np.array(S, int)
        g = S >= 0
        nev += int(g.sum())
        s = S[g]
        parts.append(mk(sym, "H1", 60, "g7", dn, -sign, s + 1, idx[s].astype(np.int64) * 3600000, atr[s] / np.abs(c[s]) * 1e4))
    return pd.concat(parts) if parts else pd.DataFrame(columns=COLS), nev


def g8_cols(ev, sym, path):
    m0, h, l, c = G8.minutes(path)
    bh, bl, bc, atr = G8.m5(h, l, c)
    want = set(ev.ts_utc_ms.values)
    rows = []
    for day in pd.date_range(pd.Timestamp(m0 * 60, unit="s").date(), pd.Timestamp((m0 + len(h)) * 60, unit="s").date()):
        b = (day - pd.Timedelta(days=1)).date()
        s0 = int(G8.dt.datetime(b.year, b.month, b.day, 19, tzinfo=G8.NY).astimezone(G8.UTC).timestamp() // 60) - m0
        if (m0 + s0) * 60000 not in want:
            continue
        e = G8.event(h, l, c, bh, bl, bc, atr, s0)
        if e is None:
            continue
        sign, i, E, rho, a, t_cl, t_end = e
        hh, ll, cc = h[t_cl:t_end], l[t_cl:t_end], c[t_cl:t_end]
        if len(hh) < 1 or not G8.clean(hh, ll, cc):
            continue
        rows.append(dict(sym=sym, tf="5m", ev="g8", dir="short" if sign < 0 else "long", side=sign, t0=i + 1,
                         tt0=(m0 + i * 5) * 60000, atr_bps=a / E * 1e4, ss_bps=rho / E * 1e4, ot1_bps=rho / E * 1e4,
                         ot1_fallback=0, tf_min=5))
    return pd.DataFrame(rows, columns=COLS), len(rows)


def g63_cols(ev, sym, path):
    idx, h, l, c, ok = G4.bars(path, 5)
    out = E1.g4_cols(ev, sym, "M5", 5, idx, h, l, c)
    out["tf"] = "M5"
    out["ev"] = "g63"
    out["tf_min"] = 5
    return out[COLS], len(out)


if __name__ == "__main__":
    kind, evp, src, outp = sys.argv[1:5]
    syms = sys.argv[5:]
    ev = pd.read_csv(evp)
    if syms:
        ev = ev[ev.coin.isin(syms)]
    if kind == "g63":
        ev = ev[ev.def_id.str.contains(":bull|:bear")]
    parts = []
    nev = 0
    for sym, e in ev.groupby("coin"):
        p = f"{src}/{sym}.csv.gz"
        if kind in ("g5", "g61"):
            o, n = asia_cols(e, sym, p, kind)
        elif kind == "g6":
            o, n = g6_cols(e, sym, p)
        elif kind == "g7":
            o, n = g7_cols(e, sym, p)
        elif kind == "g8":
            o, n = g8_cols(e, sym, p)
        else:
            o, n = g63_cols(e, sym, p)
        parts.append(o)
        nev += n
    out = pd.concat(parts) if parts else pd.DataFrame(columns=COLS)
    out.to_csv(outp, index=False, compression="gzip")
    print(outp, "rows", len(out), "events", nev, "events_file", len(ev), "ss", int(out.ss_bps.notna().sum()))
