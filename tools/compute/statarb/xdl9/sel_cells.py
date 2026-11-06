#!/usr/bin/env python3
"""TK-493 F7 шаг 4 (ПК, numpy): пары клеток, полураспад и окно W на ФОРМИРОВАНИИ; файлы заданий для калибровки entry_z (Deck).

Клетка = (вид, биржа или пара бирж, полоса). Виды: A1 (отобранные эмпирическим нулём пары), A2 (монета против BTC биржи, только крипта),
C1 и D2 (один актив на двух биржах, пары таблицы F5 c1d2 = 1; Y - нога с большим оборотом, Н20), B1 (корзины sel_b1.py).
Полураспад пары (П-37 §6.1): AR(1) спреда s_{t+1} = a + phi*s_t + e по соседним валидным минутам формирования; HL = ln2 / (-ln phi),
phi in (0, 1); пары с phi вне (0, 1) или с < 300 соседних пар (N_RET_MIN) в медиану не входят, их число - в таблице.
Спред: A1/A2/B1 - ln y - a - b*ln x (a, b - МНК по общим минутам формирования), C1/D2 - ln y - ln x (b = 1).
W = max_hold = round(4 * медиана HL) по парам клетки (П-37 §5); min_obs = W; refresh = W.
Выходы: data/xdl9/sel/cellpairs/<клетка>.csv, docs/.../cells_hl.csv, data/xdl9/sel/entry/* (задания для калибровки на Deck).
"""
import csv, math, os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sel_lib as S  # noqa: E402

W_FLOOR = 2        # технический минимум ядра: min_obs >= 2 (kernel.rs beta_z); не статистическое число
ENTRY_GRID = [1.0 + 0.25 * i for i in range(13)]       # 1,00 ... 4,00 шаг 0,25 (Н8)


class Store:
    def __init__(self):
        self.rows, self.C, self.am = S.load_formation()
        self.ci = {(d["exchange"], S.safe(d["symbol"])): i for i, d in enumerate(self.rows)}
        self.con = {(r["exchange"], S.safe(r["symbol"])): r for r in S.read_csv(os.path.join(S.SEL, "contracts.csv"))}
        self.sess = S.session_mask(self.C.shape[1])
        self._ln = {}

    def ln(self, ex, stem):
        k = (ex, stem)
        if k not in self._ln:
            i = self.ci[k]
            v = np.log(self.C[i].astype(np.float64))
            if self.con[k]["us_mask"] == "1":
                v[~self.sess] = np.nan
            self._ln[k] = v
        return self._ln[k]

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


def collect_cells(st):
    """-> {cell_id: dict(kind, ex_y, ex_x, slug, pairs=[(ex_y, y, ex_x, [x...])])}"""
    cells = {}

    def add(kind, exy, exx, slug, y, xs, exxs=None):
        cid = "%s|%s|%s" % (kind, exy if exy == exx else "~".join(sorted([exy, exx])), slug)
        c = cells.setdefault(cid, dict(kind=kind, slug=slug, pairs=[]))
        c["pairs"].append((exy, y, exx, xs))

    # A1
    for r in S.read_csv(os.path.join(S.SEL, "a1_pairs.csv")):
        ex, slug = r["catalog"].split("-", 1)
        add("A1", ex, ex, slug, r["y"], [r["x"]])
    # A2: монета против BTC той же биржи, только крипта
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
    # C1, D2
    for r in S.read_csv(os.path.join(S.DATA, "asset_pairs.csv")):
        if r["c1d2"] != "1" or r["scope"] != "между биржами":
            continue
        k1, k2 = (r["ex1"], S.safe(r["sym1"])), (r["ex2"], S.safe(r["sym2"]))
        if k1 not in st.con or k2 not in st.con:
            continue
        s1, s2 = st.con[k1]["slug"], st.con[k2]["slug"]
        if s1 != s2 or s1 is None:
            continue
        for kind in ("C1", "D2"):
            add(kind, r["ex1"], r["ex2"], s1, k1[1], [k2[1]])
    # B1
    p = os.path.join(S.SEL, "b1_baskets.csv")
    if os.path.exists(p):
        for r in S.read_csv(p):
            ex, slug = r["catalog"].split("-", 1)
            add("B1", ex, ex, slug, r["target_stem"], [r["c1"], r["c2"], r["c3"]])
    return cells


def pair_hl(st, kind, exy, y, exx, xs):
    ly = st.ln(exy, y)
    if len(xs) == 1:
        lx = st.ln(exx, xs[0])
    else:
        lx = np.mean([st.ln(exx, x) for x in xs], axis=0)
    return hl_of(ly, lx, ols=kind in ("A1", "A2", "B1"))


def main():
    st = Store()
    cells = collect_cells(st)
    out, rows = [], []
    for cid in sorted(cells):
        c = cells[cid]
        hls, n_phi_out, n_none = [], 0, 0
        pr = []
        for (exy, y, exx, xs) in c["pairs"]:
            if c["kind"] == "D2":
                pr.append((exy, y, exx, "+".join(xs), "", "", ""))
                continue
            r = pair_hl(st, c["kind"], exy, y, exx, xs)
            if r is None:
                n_none += 1
                pr.append((exy, y, exx, "+".join(xs), "", "", ""))
            else:
                phi, hl, n = r
                if hl == hl:
                    hls.append(hl)
                else:
                    n_phi_out += 1
                pr.append((exy, y, exx, "+".join(xs), "%.6f" % phi, "" if hl != hl else "%.3f" % hl, n))
        S.write_csv(os.path.join(S.SEL, "cellpairs", cid.replace("|", "_") + ".csv"),
                    ["ex_y", "y", "ex_x", "x", "phi", "hl_min", "n_pairs_obs"], pr)
        med = float(np.median(hls)) if hls else float("nan")
        W_raw = int(round(4.0 * med)) if hls else ""
        W = max(W_FLOOR, W_raw) if hls else ""
        rows.append([cid, c["kind"], c["slug"], len(c["pairs"]), len(hls), n_phi_out, n_none,
                     "" if not hls else "%.3f" % med, W_raw, W])
    S.write_csv(os.path.join(S.DOCS, "cells_hl.csv"),
                ["cell", "kind", "slug", "pairs", "hl_pairs_ok", "phi_outside_unit", "no_estimate", "hl_median_min", "W_raw_min", "W_min"], rows)
    print("клеток", len(rows))


if __name__ == "__main__":
    main()
