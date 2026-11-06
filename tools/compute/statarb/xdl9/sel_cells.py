#!/usr/bin/env python3
"""TK-493 S1 шаг 4 (ПК, numpy): пары клеток, полураспад и окно W на ФОРМИРОВАНИИ (П-37 r3.1 §5, §6.1-6.2); файлы для калибровки entry_z (Deck).

Клетка = (вид, биржа или пара бирж, полоса[, окно C1]). Виды: A1 (пары, отобранные sel_fdr.py), A2 (монета против BTC биржи, только крипта),
C1 и D2 (один актив на двух биржах, пары таблицы F5 c1d2 = 1; Y - нога с большим оборотом, Н20), B1 (корзины sel_b1.py).
Полураспад пары (§6.1): AR(1) спреда s_{t+1} = a + phi*s_t + e по соседним валидным минутам формирования; HL = ln2 / (-ln phi), phi in (0, 1); пары
с phi вне (0, 1) или с < 300 соседних пар (N_RET_MIN) в медиану не входят, их число - в таблице. Спред: A1/A2/B1 - ln y - a - b*ln x (a, b - МНК по
общим минутам формирования), C1/D2 - ln y - ln x (b = 1).
W (r3.1): W = max(2, round(m * медиана HL)) по парам клетки; m = 4 для A1, A2, B1; у C1 три клетки m in {4, 16, 64}. max_hold = W (у C1 - для
каждого из трёх окон, Н44); min_obs = refresh = W. Пол 2 - технический минимум ядра (min_obs >= 2), не статистическое число; W_raw пишется рядом.
Достижимость (§6.2 (а)): z по окну из W точек не больше z_max(W) = sqrt(W - 1); печатаются z_max, доля недостижимой части сетки и доля пар клетки с
недостижимым entry_z (у пары собственное W_пары = max(2, round(m * HL_пары)) даёт z_max ниже верхнего края сетки 4,00, то есть W_пары < 17).
HL(лаг 15) (r3.2, D14, L5-05): рядом с HL(лаг 1) печатается полураспад по AR(1) на шаге 15 минут, s_{t+15} = a + phi15*s_t + e по парам значений, обе конечны:
HL15 = 15*ln2 / (-ln phi15), phi15 in (0, 1). Лаг 1 занижен шумом измерения (отскок цены); W остальных видов и сетка окон C1 (m x HL лага 1) НЕ меняются -
HL15 диагностика, не новый множитель.
D2 (r3.2, D5 i, L3-01): пары D2 ориентируются так, что Y - нога с БОЛЬШИМ медианным интервалом выплат на формировании (ничья или нет данных - как у C1: Y - нога с большим
оборотом); интервалы берутся из sel/funding_interval_form.csv (готовит funding_intervals.py на Deck по ~/xdl9/funding_form: фандинг только формирования).
Выходы: data/xdl9/sel/cellpairs/<клетка>.csv (по базовой клетке без окна), docs/.../cells_hl.csv.
"""
import csv, math, os, sys
from collections import OrderedDict
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sel_lib as S  # noqa: E402

W_FLOOR = 2        # технический минимум ядра: min_obs >= 2 (kernel.rs beta_z); не статистическое число
ENTRY_GRID = [1.0 + 0.25 * i for i in range(13)]       # 1,00 ... 4,00 шаг 0,25 (Н8)
C1_MULTS = (4, 16, 64)                                 # окна C1 (Н37)
GRID_TOP = ENTRY_GRID[-1]


def zmax(w):
    return math.sqrt(max(w - 1, 0))


def reachable_grid(w):
    zm = zmax(w)
    return [z for z in ENTRY_GRID if z <= zm + 1e-12]


class Store:
    """Закрытия формирования по строке из файла (RowStore); ln-ряды с небольшим кэшем (без матрицы всех контрактов)."""

    def __init__(self, cap=300):
        self.rows, self.C, self.am = S.load_formation(lazy=True)
        self.ci = {(d["exchange"], S.safe(d["symbol"])): i for i, d in enumerate(self.rows)}
        self.con = {(r["exchange"], S.safe(r["symbol"])): r for r in S.read_csv(os.path.join(S.SEL, "contracts.csv"))}
        self.sess = S.session_mask(self.C.shape[1])
        self.itv = load_funding_intervals()
        self._ln = OrderedDict()
        self.cap = cap

    def ln(self, ex, stem):
        k = (ex, stem)
        if k in self._ln:
            self._ln.move_to_end(k)
            return self._ln[k]
        v = np.log(np.asarray(self.C[self.ci[k]], dtype=np.float64))
        if self.con[k]["us_mask"] == "1":
            v[~self.sess] = np.nan
        self._ln[k] = v
        if len(self._ln) > self.cap:
            self._ln.popitem(last=False)
        return v

    def slug(self, ex, stem):
        return self.con[(ex, stem)]["slug"]


def hl_of(ly, lx, ols):
    """-> (phi, hl, n) или None, если не хватает наблюдений. hl = nan, если phi вне (0, 1)."""
    ok = np.isfinite(ly) & np.isfinite(lx)
    if ok.sum() < S.N_RET_MIN + 1:
        return None
    if ols:
        x, y = lx[ok], ly[ok]
        vx = x.var()
        if vx <= 0:
            return None
        b = ((x - x.mean()) * (y - y.mean())).mean() / vx
        a = y.mean() - b * x.mean()
        s = ly - a - b * lx
    else:
        s = ly - lx
    s0, s1 = s[:-1], s[1:]
    m = np.isfinite(s0) & np.isfinite(s1)
    n = int(m.sum())
    if n < S.N_RET_MIN:
        return None
    x, y = s0[m], s1[m]
    xc = x - x.mean()
    den = float((xc * xc).sum())
    if den <= 0:
        return None
    phi = float((xc * (y - y.mean())).sum() / den)
    hl = math.log(2.0) / -math.log(phi) if 0.0 < phi < 1.0 else float("nan")
    return phi, hl, n


LAG15 = 15


def hl15_of(ly, lx, ols):
    """-> (phi15, hl15 в минутах, n) или None. Тот же спред и та же регрессия, что hl_of, но по парам (s_t, s_{t+15}); n >= N_RET_MIN пар."""
    ok = np.isfinite(ly) & np.isfinite(lx)
    if ok.sum() < S.N_RET_MIN + LAG15:
        return None
    if ols:
        x, y = lx[ok], ly[ok]
        vx = x.var()
        if vx <= 0:
            return None
        b = ((x - x.mean()) * (y - y.mean())).mean() / vx
        a = y.mean() - b * x.mean()
        s = ly - a - b * lx
    else:
        s = ly - lx
    s0, s1 = s[:-LAG15], s[LAG15:]
    m = np.isfinite(s0) & np.isfinite(s1)
    n = int(m.sum())
    if n < S.N_RET_MIN:
        return None
    x, y = s0[m], s1[m]
    xc = x - x.mean()
    den = float((xc * xc).sum())
    if den <= 0:
        return None
    phi = float((xc * (y - y.mean())).sum() / den)
    hl = LAG15 * math.log(2.0) / -math.log(phi) if 0.0 < phi < 1.0 else float("nan")
    return phi, hl, n


def load_funding_intervals():
    """-> {(биржа, символ-stem): медианный интервал выплат на формировании, часы} из sel/funding_interval_form.csv (funding_intervals.py, Deck)."""
    p = os.path.join(S.SEL, "funding_interval_form.csv")
    if not os.path.exists(p):
        raise SystemExit("нет %s: сначала funding_intervals.py на Deck (ориентация D2, r3.2 D5 i)" % p)
    out = {}
    for r in S.read_csv(p):
        if r["median_gap_h"]:
            out[(r["exchange"], r["stem"])] = float(r["median_gap_h"])
    return out


def orient_d2(itv, exy, y, exx, x):
    """Ориентация пары D2 (r3.2, D5 i): Y - нога с БОЛЬШИМ медианным интервалом выплат; ничья или нет данных - ориентация по обороту (как у C1, y уже стоит первым).
    -> (exy, y, exx, x, itv_y, itv_x, причина)."""
    iy, ix = itv.get((exy, y)), itv.get((exx, x))
    if iy is not None and ix is not None and abs(iy - ix) > 1e-9:
        if iy < ix:
            return exx, x, exy, y, ix, iy, "интервал (смена)"
        return exy, y, exx, x, iy, ix, "интервал"
    return exy, y, exx, x, iy, ix, "оборот"


def collect_cells(st):
    """-> {cell_id (без окна и руки): dict(kind, slug, pairs=[(ex_y, y, ex_x, [x...])])}"""
    cells = {}

    def add(kind, exy, exx, slug, y, xs, meta=None):
        cid = "%s|%s|%s" % (kind, exy if exy == exx else "~".join(sorted([exy, exx])), slug)
        c = cells.setdefault(cid, dict(kind=kind, slug=slug, pairs=[], meta={}))
        c["pairs"].append((exy, y, exx, xs))
        if meta is not None:
            c["meta"][(exy, y, exx, "+".join(xs))] = meta

    p = os.path.join(S.SEL, "a1_pairs.csv")
    if os.path.exists(p):
        for r in S.read_csv(p):
            ex, slug = r["catalog"].split("-", 1)
            add("A1", ex, ex, slug, r["y"], [r["x"]])
    by_ex = {}
    for (ex, stem), r in st.con.items():
        if r["slug"] == "crypto" and r["in_catalog_a"] == "1":
            by_ex.setdefault(ex, []).append((stem, r))
    for ex, lst in sorted(by_ex.items()):
        btc = [stem for stem, r in lst if r["asset_id"].split("|")[0] == "BTC"]
        if len(btc) != 1:
            continue
        for stem, r in sorted(lst):
            if stem != btc[0]:
                add("A2", ex, ex, "crypto", stem, [btc[0]])
    for r in S.read_csv(os.path.join(S.DATA, "asset_pairs.csv")):
        if r["c1d2"] != "1" or r["scope"] != "между биржами":
            continue
        k1, k2 = (r["ex1"], S.safe(r["sym1"])), (r["ex2"], S.safe(r["sym2"]))
        if k1 not in st.con or k2 not in st.con:
            continue
        s1, s2 = st.con[k1]["slug"], st.con[k2]["slug"]
        if s1 != s2 or s1 is None:
            continue
        add("C1", r["ex1"], r["ex2"], s1, k1[1], [k2[1]])
        exy, y, exx, x, iy, ix, why = orient_d2(st.itv, r["ex1"], k1[1], r["ex2"], k2[1])
        add("D2", exy, exx, s1, y, [x], meta=dict(itv_y_h=iy, itv_x_h=ix, orient=why))
    p = os.path.join(S.SEL, "b1_baskets.csv")
    if os.path.exists(p):
        for r in S.read_csv(p):
            ex, slug = r["catalog"].split("-", 1)
            add("B1", ex, ex, slug, r["target_stem"], [r["c1"], r["c2"], r["c3"]])
    return cells


def pair_hl(st, kind, exy, y, exx, xs):
    ly = st.ln(exy, y)
    lx = st.ln(exx, xs[0]) if len(xs) == 1 else np.mean([st.ln(exx, x) for x in xs], axis=0)
    return hl_of(ly, lx, ols=kind in ("A1", "A2", "B1"))


def pair_hl15(st, kind, exy, y, exx, xs):
    ly = st.ln(exy, y)
    lx = st.ln(exx, xs[0]) if len(xs) == 1 else np.mean([st.ln(exx, x) for x in xs], axis=0)
    return hl15_of(ly, lx, ols=kind in ("A1", "A2", "B1"))


def mults_of(kind):
    return C1_MULTS if kind == "C1" else (4,)


def cell_ids(cid, kind):
    """Идентификаторы клеток с окном: у C1 - cid|w4, cid|w16, cid|w64; у остальных - сам cid (m = 4)."""
    return [(cid + "|w%d" % m, m) for m in C1_MULTS] if kind == "C1" else [(cid, 4)]


def main():
    st = Store()
    cells = collect_cells(st)
    rows = []
    for cid in sorted(cells):
        c = cells[cid]
        hls, hls15, n_phi_out, n_none = [], [], 0, 0
        pr = []
        for (exy, y, exx, xs) in c["pairs"]:
            if c["kind"] == "D2":
                m = c["meta"][(exy, y, exx, "+".join(xs))]
                pr.append((exy, y, exx, "+".join(xs), "", "", "", "", "", "%g" % m["itv_y_h"] if m["itv_y_h"] is not None else "",
                           "%g" % m["itv_x_h"] if m["itv_x_h"] is not None else "", m["orient"]))
                continue
            r = pair_hl(st, c["kind"], exy, y, exx, xs)
            r15 = pair_hl15(st, c["kind"], exy, y, exx, xs)
            if r15 is not None and r15[1] == r15[1]:
                hls15.append(r15[1])
            p15 = ("%.6f" % r15[0], "" if r15[1] != r15[1] else "%.3f" % r15[1]) if r15 is not None else ("", "")
            if r is None:
                n_none += 1
                pr.append((exy, y, exx, "+".join(xs), "", "", "", p15[0], p15[1], "", "", ""))
            else:
                phi, hl, n = r
                if hl == hl:
                    hls.append(hl)
                else:
                    n_phi_out += 1
                pr.append((exy, y, exx, "+".join(xs), "%.6f" % phi, "" if hl != hl else "%.3f" % hl, n, p15[0], p15[1], "", "", ""))
        S.write_csv(os.path.join(S.SEL, "cellpairs", cid.replace("|", "_") + ".csv"),
                    ["ex_y", "y", "ex_x", "x", "phi", "hl_min", "n_pairs_obs", "phi15", "hl15_min", "itv_y_h", "itv_x_h", "orient"], pr)
        med = float(np.median(hls)) if hls else float("nan")
        med15 = float(np.median(hls15)) if hls15 else float("nan")
        for cell, m in cell_ids(cid, c["kind"]):
            if c["kind"] == "D2":
                rows.append([cell, c["kind"], c["slug"], "", len(c["pairs"]), "", "", "", "", "", "", "", "", "", "", "", "", ""])
                continue
            W_raw = int(round(m * med)) if hls else ""
            W = max(W_FLOOR, W_raw) if hls else ""
            if hls:
                zm = zmax(W)
                n_reach = len(reachable_grid(W))
                unreach_grid = 1.0 - n_reach / len(ENTRY_GRID)
                unreach_pairs = sum(1 for h in hls if max(W_FLOOR, int(round(m * h))) < 17) / len(hls)    # z_max(W_пары) < 4,00 <=> W_пары < 17
            else:
                zm = n_reach = unreach_grid = unreach_pairs = ""
            rows.append([cell, c["kind"], c["slug"], m, len(c["pairs"]), len(hls), n_phi_out, n_none,
                         "" if not hls else "%.3f" % med, W_raw, W,
                         "" if not hls else "%.3f" % zm, n_reach, "" if not hls else "%.4f" % unreach_grid,
                         "" if not hls else "%.4f" % unreach_pairs,
                         "" if not hls15 else "%.3f" % med15, len(hls15), "" if not (hls and hls15) else "%.2f" % (med15 / med)])
    S.write_csv(os.path.join(S.DOCS, "cells_hl.csv"),
                ["cell", "kind", "slug", "m", "pairs", "hl_pairs_ok", "phi_outside_unit", "no_estimate", "hl_median_min", "W_raw_min", "W_min",
                 "z_max", "grid_reachable_n", "grid_unreachable_share", "pairs_unreachable_share", "hl15_median_min", "hl15_pairs_ok", "hl15_over_hl1"], rows)
    print("клеток", len(rows))


if __name__ == "__main__":
    main()
