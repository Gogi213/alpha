#!/usr/bin/env python3
"""TK-446: кривые вместо фиксированных точек (метка/база/кластерный бутстреп — как в TK-443, tk443-forward.py импортируется).
  python3 tk446-curves.py <куб> <месяц YYYY-MM> <вых.csv>
Оси (лог-шаг ×1,25, сетки заморожены до счёта, ничего не подбирается здесь — выбор порога по кривой — в отчёте):
  A1: qr (оборот 60 м к норме 7 сут) и q15 (15 м) ≥ 2·1,25^k до ≥1000; сигнал один; D∈{90,360}; метка q99.
  A2: vr (σ1м 60 м к норме) ≥ 1,25^k до ≥20; D∈{90,360}; метка q99.
  A3: D = 90·1,25^k до 2880 мин; квантиль хода q∈{90,95,97,99,99,5,99,9}; сигнал S10 (vr≥3&qr≥3) и база; ход как в TK-442 (EV-W).
  A4: горизонт h = 5·1,25^k до 2880 мин после сигнала S10: направленный возврат (знак r60) за h — среднее/доля/лифт хвоста |ret_h|≥q99_h.
Строки: ось,сигнал,порог,D,q,напр,ячеек,база,срабат,попад,точность,полнота,подъём,подъём_lo,подъём_hi,срабат_в_сутки_на_монету.
A4-строки: база=доля хвоста |ret_h|≥q99; попад=хвост; точность=ср. напр. возврат б.п.; полнота=медиана б.п.; подъём=доля хвоста/0,01; lo/hi=CI ср. возврата б.п.
Число испытаний = число строк (печатается)."""
import importlib.util
import sys

import numpy as np

spec = importlib.util.spec_from_file_location("t443", __file__.replace("tk446-curves.py", "tk443-forward.py"))
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
MIN, STEP, W, NORM = t.MIN, t.STEP, t.W, t.NORM

BB = 100


def grid(a, step, top):
    out, v = [], a
    while v < top * 1.0001:
        out.append(v)
        v *= step
    return out


def main(cube, ym, out):
    m0, m1 = t.mb(ym)
    sy, lo, A = t.load(cube, "bybit-linear")
    keep = [i for i, s in enumerate(sy) if s.endswith("USDT") and not t.LEV.search(s)]
    sy = [sy[i] for i in keep]
    T = A.shape[2]
    c = t.ffill_c(np.array(A[keep, 3, :]))
    q = np.nan_to_num(np.array(A[keep, 4, :]))
    lc = np.log(c)
    r = np.zeros_like(lc)
    r[:, 1:] = np.nan_to_num(np.diff(lc, axis=1))
    Cr2, Cq = t.csum(r * r), t.csum(q)
    present = t.csum(np.isfinite(A[keep, 3, :]).astype(np.float64))
    g0 = max(m0 - lo, NORM + W)
    g0 += (-g0) % STEP
    ts = np.arange(g0, m1 - lo, STEP)
    ok_t = ts[ts + 90 < T]
    n = len(sy)
    i = ok_t
    ie = i - W
    s60 = np.sqrt(t.rsum(Cr2, i, W) / W)
    s7 = np.sqrt((Cr2[:, ie + 1] - Cr2[:, ie + 1 - NORM]) / NORM)
    q7 = (Cq[:, ie + 1] - Cq[:, ie + 1 - NORM]) / NORM
    with np.errstate(all="ignore"):
        vr = s60 / s7
        qr = t.rsum(Cq, i, W) / W / q7
        q15 = t.rsum(Cq, i, 15) / 15 / q7
        r60 = (lc[:, i] - lc[:, i - W]) / (s7 * np.sqrt(W))
    nz = ((t.rsum(present, i, W) >= 30) & (present[:, ie + 1] - present[:, ie + 1 - NORM] >= NORM * 0.5)
          & (s7 > 0) & (q7 > 0))
    days = len(ok_t) * STEP / 1440
    S10 = (vr >= 3) & (qr >= 3) & nz
    SIG = {}
    for th in grid(2, 1.25, 1000):
        SIG[("A1", "qr", th)] = (qr >= th) & nz
        SIG[("A1", "q15", th)] = (q15 >= th) & nz
    for th in grid(1, 1.25, 20):
        SIG[("A2", "vr", th)] = (vr >= th) & nz
    rng = np.random.default_rng(446)
    dayi = ((lo + ok_t) // 1440).astype(np.int64)
    dayi -= dayi.min()
    cid_full = np.arange(n, dtype=np.int64)[:, None] * (int(dayi.max()) + 1) + dayi[None, :]
    rows = []

    def boot(valid, NC, cidc, Wt):
        return Wt @ np.bincount(cidc[valid], minlength=NC).astype(np.float32)

    def prep(valid):
        uc, inv = np.unique(cid_full[valid], return_inverse=True)
        cidc = np.zeros(valid.shape, np.int64)
        cidc[valid] = inv
        NC = len(uc)
        Wt = rng.multinomial(NC, np.full(NC, 1.0 / NC), size=BB).astype(np.float32)
        return cidc, NC, Wt

    D3 = sorted({int(round(v)) for v in grid(90, 1.25, 2880)} | {90, 360})
    for D in D3:
        mx = np.full((n, len(ok_t)), np.nan)
        mn = np.full_like(mx, np.nan)
        for k in range(n):
            mx[k] = t.fwd(c[k], 90, D, 1)[ok_t]
            mn[k] = t.fwd(c[k], 90, D, -1)[ok_t]
        with np.errstate(all="ignore"):
            cc = c[:, ok_t]
            up, dn = mx / cc - 1, 1 - mn / cc
        valid = nz & np.isfinite(up) & np.isfinite(dn)
        if valid.sum() < 1000:
            continue
        cidc, NC, Wt = prep(valid)
        dnb = boot(valid, NC, cidc, Wt)
        qs = (90, 95, 97, 99, 99.5, 99.9)
        for qq in qs:
            L = {}
            for nm, v in (("up", up), ("dn", dn)):
                L[nm] = valid & (v >= np.quantile(v[valid], qq / 100))
            L["any"] = L["up"] | L["dn"]
            posb = {dr: boot(lb, NC, cidc, Wt) for dr, lb in L.items()}
            todo = [(("A3", "S10", 3.0), S10)]
            if D in (90, 360) and qq == 99:
                todo += list(SIG.items())
            for key, sig in todo:
                sg = sig & valid
                nf = int(sg.sum())
                nfb = boot(sg, NC, cidc, Wt) if nf else None
                for dr, lab in L.items():
                    npos = int(lab.sum())
                    base = npos / valid.sum()
                    hit = sg & lab
                    nh = int(hit.sum())
                    if nf and nh:
                        nhb = boot(hit, NC, cidc, Wt)
                        with np.errstate(all="ignore"):
                            lb_ = (nhb / nfb) / (posb[dr] / dnb)
                        llo, lhi = np.nanpercentile(lb_, [2.5, 97.5])
                    else:
                        llo = lhi = float("nan")
                    prec = nh / nf if nf else float("nan")
                    rows.append((key[0], key[1], round(key[2], 4), D, qq, dr, int(valid.sum()), round(base, 5), nf, nh,
                                 round(prec, 5), round(nh / npos, 5) if npos else float("nan"),
                                 round(prec / base, 3) if nf and base else float("nan"),
                                 round(llo, 3), round(lhi, 3), round(nf / days / max(n, 1), 4)))
        print(ym, "D", D, "rows", len(rows), flush=True)
    # A4: горизонт после сигнала S10
    for h in sorted({int(round(v)) for v in grid(5, 1.25, 2880)}):
        okh = ok_t + h < T
        if okh.sum() < 100:
            continue
        j = np.where(okh, ok_t + h, 0)
        with np.errstate(all="ignore"):
            ret = c[:, j] / c[:, ok_t] - 1
        valid = nz & okh[None, :] & np.isfinite(ret)
        if valid.sum() < 1000:
            continue
        sgn = np.sign(r60)
        dret = sgn * ret                                    # направленный возврат: знак r60 (движение за предыдущий час)
        tailx = np.quantile(np.abs(ret[valid]), 0.99)
        cidc, NC, Wt = prep(valid)
        for side, sm in (("r60>0", sgn > 0), ("r60<0", sgn < 0), ("any", np.ones_like(valid))):
            for nm, sg0 in (("база", valid), ("S10", S10 & valid)):
                sg = sg0 & sm & np.isfinite(dret)
                nf = int(sg.sum())
                if not nf:
                    continue
                x = np.where(sg, dret, 0.0)
                tl = sg & (np.abs(ret) >= tailx)
                cnt = np.bincount(cidc[sg], minlength=NC).astype(np.float32)
                sm_ = np.bincount(cidc[sg], weights=x[sg], minlength=NC).astype(np.float32)
                tl_ = np.bincount(cidc[tl], minlength=NC).astype(np.float32)
                mb_ = (Wt @ sm_) / (Wt @ cnt)
                lo_, hi_ = np.nanpercentile(mb_, [2.5, 97.5])
                rows.append(("A4", nm + ":" + side, float(h), h, 99, "dret", int(valid.sum()), round(float(tl.sum()) / nf, 5), nf,
                             int(tl.sum()), round(float(x[sg].sum()) / nf * 1e4, 3), round(float(np.median(dret[sg])) * 1e4, 3),
                             round(float(tl.sum()) / nf / 0.01, 3), round(float(lo_) * 1e4, 3), round(float(hi_) * 1e4, 3),
                             round(nf / days / max(n, 1), 4)))
        print(ym, "h", h, "rows", len(rows), flush=True)
    with open(out, "w", newline="") as f:
        f.write("ось,сигнал,порог,D,q,напр,ячеек,база,срабат,попад,точность,полнота,подъём,подъём_lo,подъём_hi,срабат_в_сутки_на_монету\n")
        for rw in rows:
            f.write(",".join(str(x) for x in rw) + "\n")
    print("rows (испытаний)", len(rows), "n", n, "days", days)


if __name__ == "__main__":
    main(*sys.argv[1:4])
