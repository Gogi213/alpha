#!/usr/bin/env python3
"""TK-448: механичность подъёма признаков TK-447 — условный подъём внутри страт по |ret24h| и vol24h (только данные <= t).
  python3 tk448-cond.py <куб> <ГГГГ-ММ> <каталог признаков> <каталог доп.признаков|-> <каталог вых.> [D=360]
Страты (v2, после возврата Судьи): |ret24h_bps| x vol24h = sqrt(mean r^2 за 1440 мин), по 23 корзины на оси (квантили 5 % шагом + 97,5/99/99,5 %) = 529 страт.
Самопроверка контроля: ret24h_bps, ctl_abs_ret24h, ctl_vol24h как признаки внутри страт должны дать разброс < 1,3.
Внутри страты признак режется на 5 квантильных корзин (ZERO: корзина 0 = ровно 0 + 5 квантилей ненулевых); метка и кластеры — как TK-447.
Условный подъём корзины = попаданий / sum(база страты по ячейкам корзины); безусловный — как TK-447, но на квинтилях месяца. CI — бутстреп кластеров монета-сутки (B=100)."""
import importlib.util
import json
import os
import sys

import numpy as np

spec = importlib.util.spec_from_file_location("t447", __file__.replace("tk448-cond.py", "tk447-features.py"))
f7 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f7)
t = f7.t
ZERO = f7.ZERO_BUCKET
NS = 5
PQ = np.concatenate([np.linspace(0.05, 0.95, 19), [0.975, 0.99, 0.995]])
NA = len(PQ) + 1
NST = NA * NA


def qbin(x, ok, zero):
    """корзины 0..5: для ZERO 0 = ровно 0, 1..5 по ненулевым; иначе 1..5 по всем ok. -1 вне ok."""
    b = np.full(x.shape, -1, np.int8)
    m = ok & (x > 0) if zero else ok
    if m.sum() < 50:
        return b
    inner = np.quantile(x[m], np.linspace(0, 1, NS + 1)[1:-1])
    b[m] = np.searchsorted(inner, x[m], "right") + 1
    if zero:
        b[ok & ~(x > 0)] = 0
    return b


def main(cube, ym, fd, ed, outd, D=360):
    os.makedirs(outd, exist_ok=True)
    sy, lo, c, h, l, q, qs, m0, m1, T = f7.prep(cube, ym)
    ts = f7.grid(lo, m0, m1, T)
    meta = json.load(open(f"{fd}/{ym}.meta.json"))
    assert meta["syms"] == sy and meta["ts"] == ts.tolist(), "сетка/монеты не совпали с TK-447"
    F = {k: v for k, v in zip(meta["names"], np.load(f"{fd}/{ym}.f.npy", mmap_mode="r"))}
    if ed != "-" and os.path.exists(f"{ed}/{ym}.meta.json"):
        me = json.load(open(f"{ed}/{ym}.meta.json"))
        F.update({k: v for k, v in zip(me["names"], np.load(f"{ed}/{ym}.f.npy", mmap_mode="r"))})
    nz = np.isfinite(F["liq_rank7d"])  # = nz из build (tv конечен только при nz)
    lab_all = f7.labels(c, ts, nz, ym)
    valid, lab, Xu, Xd = lab_all[D]
    # контроли (<= t)
    n = len(sy)
    lc = np.log(c)
    r = np.zeros_like(lc)
    r[:, 1:] = np.nan_to_num(np.diff(lc, axis=1))
    vol = np.sqrt(t.rsum(t.csum(r * r), ts, 1440) / 1440)
    ar = np.abs(np.asarray(F["ret24h_bps"], np.float64))
    del c, h, l, q, qs, lc, r
    ok0 = valid & np.isfinite(vol) & np.isfinite(ar)
    F["ctl_abs_ret24h"] = ar
    F["ctl_vol24h"] = vol
    ea = np.quantile(ar[ok0], PQ)
    ev = np.quantile(vol[ok0], PQ)
    st = np.where(ok0, np.searchsorted(ea, ar, "right") * NA + np.searchsorted(ev, vol, "right"), -1)
    nst = np.bincount(st[ok0], minlength=NST)
    base_s = np.bincount(st[ok0 & lab], minlength=NST) / np.maximum(nst, 1)
    idx0 = np.flatnonzero(ok0)
    sidx = idx0[np.argsort(st.ravel()[idx0], kind="stable")]
    sb = np.searchsorted(st.ravel()[sidx], np.arange(NST + 1))
    bs = np.where(st >= 0, base_s[np.clip(st, 0, None)], 0.0)
    base = (lab & ok0).sum() / ok0.sum()
    with open(f"{outd}/strata-{ym}.csv", "w") as fo:
        fo.write("месяц,страта_ret24h,страта_vol,ячеек,база,подъём_страты\n")
        for s in range(NST):
            fo.write(f"{ym},{s // NA + 1},{s % NA + 1},{nst[s]},{base_s[s]:.5f},{base_s[s] / base:.3f}\n")
    dayi = ((lo + ts) // f7.DAY).astype(np.int64)
    dayi -= dayi.min()
    cid = np.arange(n, dtype=np.int64)[:, None] * (int(dayi.max()) + 1) + dayi[None, :]
    uc, inv = np.unique(cid[ok0], return_inverse=True)
    cidc = np.zeros(ok0.shape, np.int64)
    cidc[ok0] = inv
    NC = len(uc)
    Wt = np.random.default_rng(448).multinomial(NC, np.full(NC, 1.0 / NC), size=f7.BB).astype(np.float32)
    posb = Wt @ np.bincount(cidc[lab & ok0], minlength=NC).astype(np.float32)
    denb = Wt @ np.bincount(cidc[ok0], minlength=NC).astype(np.float32)
    rows = []
    for nm in F:
        f = np.asarray(F[nm], np.float64)
        ok = ok0 & np.isfinite(f)
        if ok.sum() < 1000 or np.nanstd(f[ok]) == 0:
            continue
        zero = nm in ZERO
        bu = qbin(f, ok, zero)  # безусловные корзины месяца
        bc = np.full(f.shape, -1, np.int8)  # условные: внутри страты
        fr, okr, bcr = f.ravel(), ok.ravel(), bc.ravel()
        for s in range(NST):
            ii = sidx[sb[s]:sb[s + 1]]
            if len(ii) >= 50:
                bcr[ii] = qbin(fr[ii], okr[ii], zero)
        cov = ok.sum() / ok0.sum()
        base_ok = (lab & ok).sum() / ok.sum()
        for kind, b in (("безусл", bu), ("усл", bc)):
            mk = b >= 0
            key = cidc * 6 + b
            den = np.bincount(key[mk], minlength=NC * 6).reshape(NC, 6).astype(np.float32)
            pos = np.bincount(key[mk & lab], minlength=NC * 6).reshape(NC, 6).astype(np.float32)
            exp = np.bincount(key[mk], weights=bs[mk] if kind == "усл" else np.full(mk.sum(), base), minlength=NC * 6).reshape(NC, 6)
            pB, eB = Wt @ pos, Wt @ exp.astype(np.float32)
            for bi in range(6):
                if den[:, bi].sum() == 0:
                    continue
                with np.errstate(all="ignore"):
                    lf = pB[:, bi] / eB[:, bi]
                lo_, hi_ = np.nanpercentile(lf, [2.5, 97.5])
                lift = pos[:, bi].sum() / max(exp[:, bi].sum(), 1e-12)
                rows.append((ym, nm, kind, bi, int(den[:, bi].sum()), int(pos[:, bi].sum()), round(cov, 4), round(float(lift), 3), round(float(lo_), 3), round(float(hi_), 3)))
        print(ym, nm, flush=True)
    with open(f"{outd}/cond-{ym}.csv", "w") as fo:
        fo.write("месяц,признак,вид,корзина,ячеек,попаданий,покрытие,подъём,подъём_lo,подъём_hi\n")
        for rw in rows:
            fo.write(",".join(str(x) for x in rw) + "\n")
    json.dump({"ym": ym, "D": D, "base": base, "Xu": Xu, "Xd": Xd, "valid": int(ok0.sum()), "ea": ea.tolist(), "ev": ev.tolist()}, open(f"{outd}/meta-{ym}.json", "w"))
    print("done", ym, "base", round(base, 5), "rows", len(rows))


if __name__ == "__main__":
    main(*sys.argv[1:7])
