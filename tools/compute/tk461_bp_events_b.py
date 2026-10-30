#!/usr/bin/env python3
"""TK-465 (хвост TK-461, P-TK461 §12.2 п.1): bp-события G5/G6/G61/G63/G7/G8 + G2/G3/G32 (B2) -> входной csv ядра TK-463. Исходов не считает.
python tk461_bp_events_b.py <g5|g6|g61|g63|g7|g8|g2|g3|g32> <events.csv.gz bp-gN> <каталог минут csv.gz> <out.csv.gz> [SYM ...]
Колонки как у tk461_bp_events.py: sym,tf,ev,dir,side,t0,tt0,atr_bps,ss_bps,ot1_bps,ot1_fallback (+ tf_min - минут в баре tf для входа:
вход = open первой минуты >= tt0 + tf_min*60000). Набор событий = строки bp-gN-events (ключ ts,coin[,def_id]); код детекторов - модули
tools/bp_g{5,61,7,8}.py без правок (импорт). Правила, где протокол молчит (пусто = «не определено», ячейки SS/ОТ1 строки пропускаются, §12.2):
- сторона/стоп - только из P-TK458-b2-g*: G8 - сторона и ρ протокола (ss=ρ/E), G7 - сторона = ожидаемый разворот (хай-свип -> шорт),
  ss = до последнего подтверждённого свинга k=2 (sancho.swings) на t0-1 по §12.2 п.1; G5/G61/G6 - строки входов «bp-side-x2» (оба направления, dir=long|short), стоп не задан -> ss пусто.
- G5/G61: событие = первая минута снятия границы Азии, вход = open следующей минуты (tt0 = открытие минуты снятия, tf 1 мин); ATR14 минутный.
- G6: событие = окно (асия/франкфурт/лондон/ny), вход = open первой минуты окна (tt0 = старт окна - 1 ч, tf_min=60), ATR14 H1 бара до окна;
  ev=g6-<окно>, два направления.
- G7: событие = бар снятия s свинг-хая/лоя H1 (первый бар t+1..t+M с пробоем), вход = close s (tt0 = открытие s); ATR14 H1 на s.
- G8: бар i M5 (слом), вход = close i, side/ρ по протоколу; ATR14(M5) на i.
- G2/G3/G32 (CEO 22:26): вход = close бара касания/снятия s (tt0 = открытие s, t0 = s+1), ATR14 на s; ss/ot1 = до последнего подтверждённого
  свинга k=2 (sancho.swings, i+2 <= s) в рамке входа (§12.2 п.1), > 0, иначе пусто. G2: строка bp-g2 x тест (X=0,3/0,5/0,618 -> ev g2.1/g2.3/g2.2, где f не NaN),
  касание s пересчитано как bp_g2.react (f сверяется с файлом), сторона = продолжение ноги (хай-нога -> лонг), стоп - свинг-лой. G3: s = первое снятие
  фрактала k=1 в t+1..t+50, сторона = разворот (хай -> шорт). G32: уровни/касание как bp_g32.events (без контроля): ev g32a W1->D1, g32b D1->H1, g32c PDH/PDL.
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
SN = _load("sancho", os.path.join(HERE, "sancho.py"))
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
        # §12.2 п.1: стоп - последний подтверждённый (i+2 <= s) свинг k=2 по sancho.swings в рамке входа; ss = (уровень - close s)/|c|, > 0
        sw = SN.swings(hh, ok, 2)
        pos = np.searchsorted(sw + 2, s, side="right") - 1
        lv = np.where(pos >= 0, hh[sw[np.maximum(pos, 0)]] if len(sw) else np.nan, np.nan)
        ss = (lv - cc[s]) / np.abs(c[s]) * 1e4
        ss = np.where(np.isfinite(ss) & (ss > 0), ss, np.nan)
        parts.append(mk(sym, "H1", 60, "g7", dn, -sign, s + 1, idx[s].astype(np.int64) * 3600000, atr[s] / np.abs(c[s]) * 1e4, ss))
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


M_, N_ = 50, 10


def swing_ss(src, ok, lvl, c_s, s, sgn):
    """ss_bps до последнего подтверждённого (i+2 <= s) свинга k=2 по sancho.swings(src): уровень lvl[i]; sgn=+1 стоп над входом, -1 под входом; > 0, иначе nan"""
    sw = SN.swings(src, ok, 2)
    pos = np.searchsorted(sw + 2, s, side="right") - 1
    lv = np.where(pos >= 0, lvl[sw[np.maximum(pos, 0)]] if len(sw) else np.nan, np.nan)
    ss = sgn * (lv - c_s) / np.abs(c_s) * 1e4
    return np.where(np.isfinite(ss) & (ss > 0), ss, np.nan)


def pack(sym, tf_name, tf, ev, dn, side, s, bar_idx, atr, c, ss):
    """s - индексы баров входа (рамка), bar_idx - номер бара tf от эпохи на каждый s"""
    s = np.asarray(s, dtype=np.int64)
    return mk(sym, tf_name, tf, ev, dn, side, s + 1, np.asarray(bar_idx, dtype=np.int64) * tf * 60000, atr[s] / np.abs(c[s]) * 1e4, ss)


def g2_touch(h, l, c, s0, touch, up, kind, bound):
    """как bp_g2.react, но -> (s, f); нет касания -> None"""
    a = np.flatnonzero(l[s0:s0 + M_] <= touch)
    if len(a) == 0:
        return None
    b = np.flatnonzero(h[s0:s0 + M_] > up)
    if len(b) and b[0] <= a[0]:
        return None
    s = s0 + int(a[0])
    return s, float(l[s:s + N_ + 1].min() > bound) if kind == "low" else float(c[s + N_] > touch)


G2TESTS = [(0.3, "low", 0.5, "g2.1"), (0.5, "close", None, "g2.3"), (0.618, "close", None, "g2.2")]


def g2_cols(ev, sym, path):
    parts, nev = [], 0
    for name, tf in G4.TFS.items():
        idx, h0, l0, c0, ok = G4.bars(path, tf)
        for sign, dn in ((1, "high"), (-1, "low")):
            x = ev[(ev.tf == name) & (ev.def_id == "def.pd_react:" + dn)]
            if not len(x):
                continue
            h, l, c = (h0, l0, c0) if sign > 0 else (-l0, -h0, -c0)
            n = len(h)
            atr = G4.atr14(h, l, c)
            pl = np.zeros(n, bool)
            pl[2:n - 2] = (l[2:n - 2] < l[1:n - 3]) & (l[2:n - 2] < l[:n - 4]) & (l[2:n - 2] < l[3:n - 1]) & (l[2:n - 2] < l[4:])
            lows = np.flatnonzero(pl)
            T = (x.ts_utc_ms.values // (tf * 60000) - 1 - idx[0]).astype(int)
            S = {e[3]: [] for e in G2TESTS}
            for tt, fs in zip(T, x[["f0", "f1", "f2"]].values):
                b = tt - 2
                a = lows[np.searchsorted(lows, b - 2, side="right") - 1]
                Lg = h[b] - l[a]
                for (xx, kind, xb, evn), fv in zip(G2TESTS, fs):
                    if np.isnan(fv):
                        continue
                    r = g2_touch(h, l, c, tt + 1, h[b] - xx * Lg, h[b], kind, h[b] - xb * Lg if xb else 0.0)
                    assert r is not None and r[1] == fv, (sym, name, dn, evn, tt)
                    S[evn].append(r[0])
            for evn, v in S.items():
                if not v:
                    continue
                s = np.array(v)
                ss = swing_ss(-l, ok, l, c[s], s, -1)
                parts.append(pack(sym, name, tf, evn, dn, sign, s, idx[s], atr, c, ss))
                nev += len(s)
    return (pd.concat(parts) if parts else pd.DataFrame(columns=COLS)), nev


def g3_cols(ev, sym, path):
    parts, nev = [], 0
    for name, tf in G4.TFS.items():
        idx, h0, l0, c0, ok = G4.bars(path, tf)
        for sign, dn in ((1, "high"), (-1, "low")):
            x = ev[(ev.tf == name) & (ev.def_id == "def.sweep_k1:" + dn)]
            if not len(x):
                continue
            h, l, c = (h0, l0, c0) if sign > 0 else (-l0, -h0, -c0)
            atr = G4.atr14(h, l, c)
            T = (x.ts_utc_ms.values // (tf * 60000) - 1 - idx[0]).astype(int)
            s = []
            for tt, fv in zip(T, x.f0.values):
                a = np.flatnonzero(h[tt + 1:tt + 1 + M_] > h[tt - 1])
                assert len(a) and float(c[tt + 1 + a[0] + N_] < h[tt - 1]) == fv, (sym, name, dn, tt)
                s.append(tt + 1 + int(a[0]))
            s = np.array(s)
            ss = swing_ss(h, ok, h, c[s], s, 1)
            parts.append(pack(sym, name, tf, "g3", dn, -sign, s, idx[s], atr, c, ss))
            nev += len(s)
    return (pd.concat(parts) if parts else pd.DataFrame(columns=COLS)), nev


def g32_cols(ev, sym, path):
    """уровни и касание - копия bp_g32.events без контроля (набор событий от контроля не зависит); число строк = bp-g32-events"""
    G32 = _load("bp_g32", os.path.join(TOOLS, "bp_g32.py"))
    d = pd.read_csv(path, usecols=[0, 1, 2, 3, 4])
    d.columns = ["t", "o", "h", "l", "c"]
    W1, D1, H1 = G32.bars(d, 10080, G32.MON), G32.bars(d, 1440), G32.bars(d, 60)
    keys, miss = {}, [0]
    for tfn, de, ts in zip(ev.tf, ev.def_id, ev.ts_utc_ms):
        k_ = keys.setdefault((tfn, de.split(":")[-1]), {})
        k_[int(ts)] = k_.get(int(ts), 0) + 1
    parts, nev = [], 0
    for nm, ek, H, Mb, mode in [("G3.2 W1-фрактал→D1", "g32a", W1, D1, "frac"), ("G3.3 D1-фрактал→H1", "g32b", D1, H1, "frac"),
                                ("G3.3 PDH/PDL→H1", "g32c", D1, H1, "pd")]:
        for sign, dn in ((1, "high"), (-1, "low")):
            Hs, Ms = (H, Mb) if sign > 0 else (G32.flip(H), G32.flip(Mb))
            h, l, c, ok, tf = Ms["h"], Ms["l"], Ms["c"], Ms["ok"], Ms["tf"]
            n = len(h)
            atr = G4.atr14(h, l, c)
            okc = np.r_[0, np.cumsum(ok)]
            hh, hok = Hs["h"], Hs["ok"]
            nh = len(hh)
            if mode == "frac":
                bs = [b for b in range(1, nh - 1) if hok[b - 1] and hok[b] and hok[b + 1] and hh[b] > hh[b - 1] and hh[b] > hh[b + 1]]
                t0s = [Hs["st"][b + 2] if b + 2 < nh else Hs["st"][b + 1] + Hs["tf"] * 60000 for b in bs]
                wm = G32.M * Hs["tf"] // tf
            else:
                bs = [b for b in range(0, nh - 1) if hok[b]]
                t0s = [Hs["st"][b + 1] for b in bs]
                wm = 24
            lv = [hh[b] for b in bs]
            S = []
            for t0, L in zip(t0s, lv):
                s0 = int(np.searchsorted(Ms["st"], t0))
                if s0 < 2 or s0 + wm + N_ + 1 >= n:
                    continue
                a = np.flatnonzero(h[s0:s0 + wm] > L)
                if len(a) == 0:
                    continue
                s = s0 + int(a[0])
                if s + N_ >= n or okc[s + N_ + 1] - okc[s - 1] != N_ + 2 or np.isnan(atr[s - 1]) or atr[s - 1] <= 0:
                    continue
                if L - c[s - 1] <= 0:
                    continue
                S.append(s)
            # события - только из bp-g32-events (P-TK461 стр.7): ключ = монета x ТФ-имя x сторона x ts (= открытие бара касания + tf)
            keep = keys.get((nm, dn), {})
            got = {int(Ms["st"][x]) + tf * 60000 for x in S}
            miss[0] += len(set(keep) - got)  # события файла, не воспроизведённые на текущих свечах
            left, S2 = dict(keep), []  # кратность ключа - как в файле (на обновлённых свечах уровней на баре могло стать больше)
            for x in S:
                k = int(Ms["st"][x]) + tf * 60000
                if left.get(k, 0) > 0:
                    left[k] -= 1
                    S2.append(x)
            S = S2
            if not S:
                continue
            s = np.array(S)
            ss = swing_ss(h, ok, h, c[s], s, 1)
            parts.append(pack(sym, "D1" if tf == 1440 else "H1", tf, ek, dn, -sign, s, Ms["st"][s] // (tf * 60000), atr, c, ss))
            nev += len(s)
    if miss[0]:
        print(sym, "g32 событий файла не воспроизведено:", miss[0], file=sys.stderr)
    return (pd.concat(parts) if parts else pd.DataFrame(columns=COLS)), nev


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
        elif kind == "g2":
            o, n = g2_cols(e, sym, p)
        elif kind == "g3":
            o, n = g3_cols(e, sym, p)
        elif kind == "g32":
            o, n = g32_cols(e, sym, p)
        elif kind == "g8":
            o, n = g8_cols(e, sym, p)
        else:
            o, n = g63_cols(e, sym, p)
        parts.append(o)
        nev += n
    out = pd.concat(parts) if parts else pd.DataFrame(columns=COLS)
    out.to_csv(outp, index=False, compression="gzip")
    nfile = int(ev[["f0", "f1", "f2"]].notna().sum().sum()) if kind == "g2" else len(ev)
    print(outp, "rows", len(out), "events", nev, "events_file", nfile, "ss", int(out.ss_bps.notna().sum()))
