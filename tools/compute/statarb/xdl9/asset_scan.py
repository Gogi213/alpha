#!/usr/bin/env python3
"""TK-493 F5: проверка полноты имён - «сироты» (основные контракты, чьё имя есть только на одной бирже) против ВСЕХ контрактов других бирж.

Те же статистики, что у pair_stats (corr минутных доходностей по общим минутам; S = σ(lr)/σ(ln c1); n_ret), но через матричные произведения
в float64 (ряд логарифмов центрируется по собственному среднему ноги: var(lr) не меняется от сдвига, а точность не теряется).
Кандидат в «пропущенную пару»: другая биржа, n_ret >= n_ret_min, S < stab_hi (нижняя граница известных «разных»), корреляция выводится.
Только формирование 01.10-14.10. Вывод: data/xdl9/asset_scan.csv.
"""
import collections, csv, json, math, os
import numpy as np
import asset_lib as L, asset_cands as A


def masked_stats(Cx, Cy, chunk=700):
    """Cx: [r, T] float32 закрытия (NaN), Cy: [c, T]. Возвращает dict массивов [r, c]: n_ret, corr, nv, S_x (σ(lr)/σ(ln x)), S_y, ratio."""
    r, T = Cx.shape
    c = Cy.shape[0]
    lx_raw = np.log(Cx.astype(np.float64))
    ly_raw = np.log(Cy.astype(np.float64))
    Mvx = np.isfinite(lx_raw); Mvy = np.isfinite(ly_raw)
    mx = np.nanmean(np.where(Mvx, lx_raw, np.nan), axis=1)
    my = np.nanmean(np.where(Mvy, ly_raw, np.nan), axis=1)
    lx = np.where(Mvx, lx_raw - mx[:, None], 0.0)
    ly = np.where(Mvy, ly_raw - my[:, None], 0.0)
    rx = np.full((r, T), np.nan); rx[:, 1:] = lx_raw[:, 1:] - lx_raw[:, :-1]
    ry = np.full((c, T), np.nan); ry[:, 1:] = ly_raw[:, 1:] - ly_raw[:, :-1]
    Mrx = np.isfinite(rx); Mry = np.isfinite(ry)
    X = np.where(Mrx, rx, 0.0); Y = np.where(Mry, ry, 0.0)
    Mrxf = Mrx.astype(np.float64); Mryf = Mry.astype(np.float64)
    Mvxf = Mvx.astype(np.float64); Mvyf = Mvy.astype(np.float64)
    out = {k: np.full((r, c), np.nan) for k in ("n_ret", "corr", "nv", "S_x", "S_y", "ratio")}
    for a in range(0, c, chunk):
        b = min(c, a + chunk)
        Yc, Mrc = Y[a:b], Mryf[a:b]
        n = Mrxf @ Mrc.T
        sx = X @ Mrc.T
        sy = Mrxf @ Yc.T
        sxx = (X * X) @ Mrc.T
        syy = Mrxf @ (Yc * Yc).T
        sxy = X @ Yc.T
        with np.errstate(all="ignore"):
            cov = sxy - sx * sy / n
            vx = sxx - sx * sx / n
            vy = syy - sy * sy / n
            corr = cov / np.sqrt(vx * vy)
        out["n_ret"][:, a:b] = n
        out["corr"][:, a:b] = corr
        lyc, mvc = ly[a:b], Mvyf[a:b]
        nv = Mvxf @ mvc.T
        Sx = lx @ mvc.T
        Sy = Mvxf @ lyc.T
        Sxx = (lx * lx) @ mvc.T
        Syy = Mvxf @ (lyc * lyc).T
        Sxy = lx @ lyc.T
        with np.errstate(all="ignore"):
            var_lr = (Sxx + Syy - 2 * Sxy) / nv - ((Sx - Sy) / nv) ** 2
            var_x = Sxx / nv - (Sx / nv) ** 2
            var_y = Syy / nv - (Sy / nv) ** 2
            out["S_x"][:, a:b] = np.sqrt(np.maximum(var_lr, 0)) / np.sqrt(np.maximum(var_x, 1e-300))
            out["S_y"][:, a:b] = np.sqrt(np.maximum(var_lr, 0)) / np.sqrt(np.maximum(var_y, 1e-300))
            out["ratio"][:, a:b] = np.exp((Sx - Sy) / nv + (mx[:, None] - my[None, a:b]))
        out["nv"][:, a:b] = nv
    return out


def main():
    rows, C = L.load_contracts()
    A.build(rows, C)
    amap = {(r["exchange"], r["symbol"]): r for r in csv.DictReader(open(os.path.join(L.DATA, "asset_map.csv"), encoding="utf-8"))}
    cal = json.load(open(os.path.join(L.DATA, "calib_summary.json"), encoding="utf-8"))
    s_hi = cal["S"]["best_diff"]; s_mid = cal["S"]["mid"]
    n_ret_min = 300
    for d in rows:
        d["role"] = amap[(d["exchange"], d["symbol"])]["role"]
    uni = [d for d in rows if d["role"] in ("main", "dup")]
    kx = collections.defaultdict(set)
    for d in uni:
        kx[d["key"]].add(d["exchange"])
    orph = [d for d in uni if d["role"] == "main" and len(kx[d["key"]]) == 1]
    print("сирот", len(orph), "всего в универсуме", len(uni))
    st = masked_stats(C[[d["i"] for d in orph]], C[[d["i"] for d in uni]])
    out = []
    for a, d in enumerate(orph):
        for b, e in enumerate(uni):
            if e["exchange"] == d["exchange"]:
                continue
            n = st["n_ret"][a, b]
            if not (n >= n_ret_min):
                continue
            # c1 - нога с большим оборотом (как pair_stats)
            Sv = st["S_x"][a, b] if d["turnover"] >= e["turnover"] else st["S_y"][a, b]
            if not (Sv < s_hi):
                continue
            out.append(dict(ex1=d["exchange"], sym1=d["symbol"], key1=d["key"], big1=d["big"], ex2=e["exchange"], sym2=e["symbol"],
                            key2=e["key"], big2=e["big"], role2=e["role"], n_ret=int(n), corr=st["corr"][a, b], S=Sv,
                            ratio=st["ratio"][a, b], n_common=int(st["nv"][a, b]), turnover1=d["turnover"], turnover2=e["turnover"]))
    out.sort(key=lambda r: (r["sym1"], -r["corr"]))
    with open(os.path.join(L.DATA, "asset_scan.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
        w.writeheader()
        for r in out:
            rr = dict(r)
            for k in ("corr", "S"):
                rr[k] = "%.5f" % rr[k]
            rr["ratio"] = "%.6g" % rr["ratio"]
            rr["turnover1"] = "%.6g" % rr["turnover1"]; rr["turnover2"] = "%.6g" % rr["turnover2"]
            w.writerow(rr)
    print("кандидатов (S < %.4f, n_ret >= %d):" % (s_hi, n_ret_min), len(out))


if __name__ == "__main__":
    main()
