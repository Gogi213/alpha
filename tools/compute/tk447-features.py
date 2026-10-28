#!/usr/bin/env python3
"""TK-447: таблица признаков v2 на кубе TK-436/440 (Bybit linear USDT, сетка 5 мин; только данные <= t).
  python3 tk447-features.py <куб> <ГГГГ-ММ> <каталог вых.> <x90.json>      — признаки + подъём по децилям
  python3 tk447-features.py --test <куб> <ГГГГ-ММ>                         — тест «нет заглядывания вперёд» (20 монет, 3 суток)
Метка и бутстреп — как в TK-443/446 (up/dn/any, X_D = q99 своего месяца, D∈{90,360,1440}, кластеры монета-сутки, B=100).
Исключение: g0 = NORM+W+90 (запас 90 мин под историю событий). x90.json — X_90(up/dn) сентября (как TK-440: X сентября без пересчёта);
нет файла → считается по этому месяцу и пишется (запускать сентябрь первым).
Без назначенных порогов; единственные фиксированные числа — определения долей (vr>=3, qr>=3 — как S10 TK-442; |z60|>=3)."""
import importlib.util
import json
import os
import sys

import numpy as np

spec = importlib.util.spec_from_file_location("t443", __file__.replace("tk447-features.py", "tk443-forward.py"))
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
MIN, STEP, W, NORM = t.MIN, t.STEP, t.W, t.NORM
DAY = 1440
BB = 100


def roll_max(x, w):
    """скользящий максимум окна w (блоков) по оси 1, значение в j = max x[j-w+1..j] (NaN/−inf до заполнения), удвоением."""
    n, m = x.shape
    cur = x.copy()
    k = 1
    out = None
    pw = {}
    while k * 2 <= w:
        nxt = cur.copy()
        nxt[:, k:] = np.maximum(cur[:, k:], cur[:, :-k])
        pw[k * 2] = nxt
        cur = nxt
        k *= 2
    base = cur  # окно k
    res = base.copy()
    rest = w - k
    if rest:
        sh = np.full_like(base, -np.inf)
        sh[:, rest:] = base[:, :-rest]
        res = np.maximum(base, sh)
    res[:, : w - 1] = -np.inf
    return res


def blocks(H, lo_fn):
    """5-мин блоки, кончающиеся на индексе 5j: минуты 5j-4..5j (j>=1); блок 0 — пустой."""
    n, T = H.shape
    m = (T - 1) // 5
    core = H[:, 1 : 1 + 5 * m].reshape(n, m, 5)
    b = lo_fn(core, axis=2)
    return np.concatenate([np.full((n, 1), np.nan), b], axis=1)  # индекс j -> блок, кончающийся на 5j


def build(c, h, l, q, qs, sy, lo, ts, x90):
    """c,h,l,q: [n,T] (c с заполнением вперёд, q без NaN), qs: [n,T] оборот спота или NaN-строка; ts — индексы t (кратны 5). -> dict имя -> [n,len(ts)]"""
    n, T = c.shape
    lc = np.log(c)
    r = np.zeros_like(lc)
    r[:, 1:] = np.nan_to_num(np.diff(lc, axis=1))
    Cr2, Cq, Ca = t.csum(r * r), t.csum(q), t.csum(np.abs(r))
    present = t.csum(np.isfinite(h).astype(np.float64))
    i = ts
    ie = i - W
    s7 = np.sqrt((Cr2[:, ie + 1] - Cr2[:, ie + 1 - NORM]) / NORM)
    q7 = (Cq[:, ie + 1] - Cq[:, ie + 1 - NORM]) / NORM
    with np.errstate(all="ignore"):
        s60 = np.sqrt(t.rsum(Cr2, i, W) / W)
        vr = s60 / s7
        qr = t.rsum(Cq, i, W) / W / q7
        z60 = (lc[:, i] - lc[:, i - W]) / (s7 * np.sqrt(W))
    nz = ((t.rsum(present, i, W) >= 30) & (present[:, ie + 1] - present[:, ie + 1 - NORM] >= NORM * 0.5)
          & (s7 > 0) & (q7 > 0))
    F = {}
    # (3) форма графика
    for nm, hh in (("ret5m", 5), ("ret15m", 15), ("ret1h", 60), ("ret4h", 240), ("ret24h", 1440)):
        F[nm + "_bps"] = (lc[:, i] - lc[:, i - hh]) * 1e4
    Hb, Lb = blocks(h, np.nanmax), blocks(l, np.nanmin)
    j = i // 5
    hs = s7 * np.sqrt(60)
    for nm, d in (("1d", 1), ("3d", 3), ("7d", 7)):
        wb = d * DAY // 5
        hm = roll_max(np.nan_to_num(Hb, nan=-np.inf), wb)[:, j]
        lm = -roll_max(np.nan_to_num(-Lb, nan=-np.inf), wb)[:, j]
        hm = np.where(np.isfinite(hm), hm, np.nan)
        lm = np.where(np.isfinite(lm), lm, np.nan)
        with np.errstate(all="ignore"):
            dmx, dmn = np.log(hm / c[:, i]) * 1e4, np.log(c[:, i] / lm) * 1e4
        F[f"dmax{nm}_bps"], F[f"dmin{nm}_bps"] = dmx, dmn
        with np.errstate(all="ignore"):
            F[f"dmax{nm}_sig"], F[f"dmin{nm}_sig"] = dmx / 1e4 / hs, dmn / 1e4 / hs
    for nm, hh in (("1h", 60), ("4h", 240)):
        wb = hh // 5
        hm = roll_max(np.nan_to_num(Hb, nan=-np.inf), wb)[:, j]
        lm = -roll_max(np.nan_to_num(-Lb, nan=-np.inf), wb)[:, j]
        with np.errstate(all="ignore"):
            F["sq" + nm + "_sig"] = np.where(np.isfinite(hm) & np.isfinite(lm), np.log(hm / lm), np.nan) / (s7 * np.sqrt(hh))
    # (1) рынок целиком (по монетам с определёнными нормами, nz)
    Mz = nz.astype(np.float64)
    cnt = np.maximum(Mz.sum(0), 1)

    def share(cond):
        return ((cond & nz).sum(0) / cnt)[None, :] * np.ones((n, 1))

    with np.errstate(all="ignore"):
        F["m_vr3"], F["m_qr3"], F["m_z3"] = share(vr >= 3), share(qr >= 3), share(np.abs(z60) >= 3)
        for nm, hh in (("15m", 15), ("1h", 60), ("24h", 1440)):
            d = lc[:, i] - lc[:, i - hh]
            F["m_up" + nm], F["m_dn" + nm] = share(d > 0), share(d < 0)
        b = sy.index("BTCUSDT") if "BTCUSDT" in sy else None
        for nm, src in (("btc_z60", z60), ("btc_vr", vr), ("btc_qr", qr), ("btc_ret24h_bps", F["ret24h_bps"])):
            F[nm] = np.ones((n, 1)) * (src[b][None, :] if b is not None else np.nan)
    # (2) история монеты: события D=90 (X сентября), реализованные к t (s <= t-90)
    with np.errstate(all="ignore"):
        x = np.full_like(c, np.nan)
        x[:, : T - 90] = c[:, 90:] / c[:, : T - 90] - 1
        e = np.isfinite(x) & ((x >= x90[0]) | (-x >= x90[1]))
        Ce = t.csum(e.astype(np.float64))
        se = i - 90
        for nm, d in (("1d", 1), ("3d", 3), ("7d", 7)):
            F["ev90_" + nm] = Ce[:, se + 1] - Ce[:, se + 1 - d * DAY]
        last = np.where(e, np.arange(T)[None, :], -1)
        np.maximum.accumulate(last, axis=1, out=last)
        ls = last[:, se]
        F["ev90_hrs_since"] = np.where(ls >= 0, (i[None, :] - ls) / 60.0, np.nan)
        F["ret7d_bps"] = (lc[:, i] - lc[:, i - 7 * DAY]) * 1e4
        F["path7d_bps"] = (Ca[:, i + 1] - Ca[:, i + 1 - 7 * DAY]) * 1e4
    # (5) ликвидность
    to7 = Cq[:, i + 1] - Cq[:, i + 1 - 7 * DAY]
    to24 = Cq[:, i + 1] - Cq[:, i + 1 - DAY]
    with np.errstate(all="ignore"):
        tv = np.where(nz, to7, np.nan)
        rk = np.argsort(np.argsort(np.where(np.isfinite(tv), tv, -1.0), axis=0), axis=0).astype(np.float64)
        nn = np.isfinite(tv).sum(0)
        F["liq_rank7d"] = np.where(np.isfinite(tv), (rk - (n - nn)[None, :]) / np.maximum(nn - 1, 1)[None, :], np.nan)
        F["liq_share24h"] = np.where(nz, to24 / np.where(nz, to24, 0).sum(0)[None, :], np.nan)
        Cqs = t.csum(np.nan_to_num(qs))
        sp7 = Cqs[:, i + 1] - Cqs[:, i + 1 - 7 * DAY]
        haveS = np.isfinite(qs).any(axis=1)[:, None]
        F["spot_fut_to7d_ln"] = np.where(haveS & (sp7 > 0) & (to7 > 0), np.log(sp7 / to7), np.nan)
    # (4) время (UTC)
    tm = lo + i
    hod = (tm % DAY) / DAY * 2 * np.pi
    dow = ((tm // DAY + 3) % 7 + (tm % DAY) / DAY) / 7 * 2 * np.pi
    for nm, v in (("hod_sin", np.sin(hod)), ("hod_cos", np.cos(hod)), ("dow_sin", np.sin(dow)), ("dow_cos", np.cos(dow))):
        F[nm] = np.ones((n, 1)) * v[None, :]
    return {k: np.asarray(v, np.float32) for k, v in F.items()}, nz


def prep(cube, ym, hi=None, nsym=None):
    m0, m1 = t.mb(ym)
    sy, lo, A = t.load(cube, "bybit-linear")
    ss, slo, S = t.load(cube, "bybit-spot")
    assert slo == lo
    keep = [k for k, s in enumerate(sy) if s.endswith("USDT") and not t.LEV.search(s)]
    if nsym:
        keep = [k for k in keep if sy[k] in nsym]
    sy = [sy[k] for k in keep]
    T = A.shape[2] if hi is None else hi
    c = t.ffill_c(np.array(A[keep, 3, :T]))
    h, l = np.array(A[keep, 1, :T]), np.array(A[keep, 2, :T])
    q = np.nan_to_num(np.array(A[keep, 4, :T]))
    sp = {s: k for k, s in enumerate(ss)}
    qs = np.full_like(c, np.nan)
    for k, s in enumerate(sy):
        if s in sp:
            qs[k] = np.array(S[sp[s], 4, :T])
    return sy, lo, c, h, l, q, qs, m0, m1, T


def grid(lo, m0, m1, T):
    g0 = max(m0 - lo, NORM + W + 90)
    g0 += (-g0) % STEP
    ts = np.arange(g0, m1 - lo, STEP)
    return ts[ts + 90 < T]


def labels(c, ts, nz, ym):
    n, T = c.shape
    out = {}
    cc = c[:, ts]
    for D in (90, 360, 1440):
        mx, mn = np.full((n, len(ts)), np.nan), np.full((n, len(ts)), np.nan)
        for k in range(n):
            mx[k], mn[k] = t.fwd(c[k], 90, D, 1)[ts], t.fwd(c[k], 90, D, -1)[ts]
        with np.errstate(all="ignore"):
            up, dn = mx / cc - 1, 1 - mn / cc
        valid = nz & np.isfinite(up) & np.isfinite(dn)
        Xu, Xd = float(np.quantile(up[valid], 0.99)), float(np.quantile(dn[valid], 0.99))
        out[D] = (valid, (up >= Xu) | (dn >= Xd), Xu, Xd)
    return out


def main(cube, ym, outd, xjson):
    os.makedirs(outd, exist_ok=True)
    sy, lo, c, h, l, q, qs, m0, m1, T = prep(cube, ym)
    ts = grid(lo, m0, m1, T)
    n = len(sy)
    LB = labels(c, ts, np.ones((n, len(ts)), bool), ym)  # временно; nz пересчитаем ниже
    if os.path.exists(xjson):
        x90 = json.load(open(xjson))
    else:
        v, _, xu, xd = LB[90]
        x90 = [xu, xd]
        json.dump(x90, open(xjson, "w"))
    F, nz = build(c, h, l, q, qs, sy, lo, ts, x90)
    LB = labels(c, ts, nz, ym)
    names = list(F)
    np.save(f"{outd}/{ym}.f.npy", np.stack([F[k] for k in names]))
    json.dump({"names": names, "syms": sy, "ts": ts.tolist(), "lo": lo, "x90": x90}, open(f"{outd}/{ym}.meta.json", "w"))
    dayi = ((lo + ts) // DAY).astype(np.int64)
    dayi -= dayi.min()
    nd = int(dayi.max()) + 1
    cid = np.arange(n, dtype=np.int64)[:, None] * nd + dayi[None, :]
    rng = np.random.default_rng(447)
    rows = []
    for D, (valid, lab, Xu, Xd) in LB.items():
        uc, inv = np.unique(cid[valid], return_inverse=True)
        cidc = np.zeros(valid.shape, np.int64)
        cidc[valid] = inv
        NC = len(uc)
        Wt = rng.multinomial(NC, np.full(NC, 1.0 / NC), size=BB).astype(np.float32)
        nvalid, npos = int(valid.sum()), int((lab & valid).sum())
        base = npos / nvalid
        posb = Wt @ np.bincount(cidc[lab & valid], minlength=NC).astype(np.float32)
        denb = Wt @ np.bincount(cidc[valid], minlength=NC).astype(np.float32)
        for k, nm in enumerate(names):
            f = F[nm]
            ok = valid & np.isfinite(f)
            cov = ok.sum() / nvalid
            if ok.sum() < 1000 or np.nanstd(f[ok]) == 0:
                rows.append((ym, nm, D, 0, "", "", int(ok.sum()), 0, round(cov, 4), round(base, 5), "", "", ""))
                continue
            edges = np.unique(np.quantile(f[ok], np.linspace(0, 1, 11)))
            nb = len(edges) - 1
            b = np.clip(np.searchsorted(edges, f, "right") - 1, 0, nb - 1)
            key = cidc * nb + b
            den = np.bincount(key[ok], minlength=NC * nb).reshape(NC, nb).astype(np.float32)
            pos = np.bincount(key[ok & lab], minlength=NC * nb).reshape(NC, nb).astype(np.float32)
            dB, pB = Wt @ den, Wt @ pos
            for bi in range(nb):
                nbn, nbp = int(den[:, bi].sum()), int(pos[:, bi].sum())
                rate = nbp / max(nbn, 1)
                with np.errstate(all="ignore"):
                    lf = (pB[:, bi] / dB[:, bi]) / (posb / denb)
                lo_, hi_ = np.nanpercentile(lf, [2.5, 97.5])
                rows.append((ym, nm, D, bi + 1, round(float(edges[bi]), 5), round(float(edges[bi + 1]), 5), nbn, nbp,
                             round(cov, 4), round(base, 5), round(rate / base, 3) if base else "", round(lo_, 3), round(hi_, 3)))
        print(ym, D, "X", round(Xu, 5), round(Xd, 5), "valid", nvalid, "base", round(base, 5), flush=True)
    with open(f"{outd}/lift-{ym}.csv", "w", newline="") as fo:
        fo.write("месяц,признак,D,дециль,от,до,ячеек,попаданий,покрытие,база,подъём,подъём_lo,подъём_hi\n")
        for rw in rows:
            fo.write(",".join(str(x) for x in rw) + "\n")
    print("features", len(names), "rows", len(rows), "n", n, "ts", len(ts))


def test(cube, ym):
    """Нет заглядывания вперёд: признак в t не меняется при порче данных > t (20 монет, 3 суток)."""
    rng = np.random.default_rng(1)
    sy0, lo, A = t.load(cube, "bybit-linear")
    cand = [s for s in sy0 if s.endswith("USDT") and not t.LEV.search(s) and s != "BTCUSDT"]
    pick = set(["BTCUSDT"] + [cand[k] for k in rng.choice(len(cand), 19, replace=False)])
    m0, m1 = t.mb(ym)
    a0 = (m0 - lo) + 12 * DAY
    sy, lo, c, h, l, q, qs, _, _, T = prep(cube, ym, hi=a0 + 3 * DAY + 100, nsym=pick)
    ts = np.arange(a0 - a0 % STEP, a0 - a0 % STEP + 3 * DAY, STEP)
    ts = ts[ts + 90 < T]
    x90 = [0.05, 0.05]
    F0, nz0 = build(c, h, l, q, qs, sy, lo, ts, x90)
    t0 = ts[len(ts) // 2]
    c2, h2, l2, q2, qs2 = c.copy(), h.copy(), l.copy(), q.copy(), qs.copy()
    for a in (c2, h2, l2, q2):
        a[:, t0 + 1 :] *= rng.uniform(0.5, 2.0, a[:, t0 + 1 :].shape)
    qs2[:, t0 + 1 :] *= 3
    F1, _ = build(c2, h2, l2, q2, qs2, sy, lo, ts, x90)
    keep = ts <= t0
    bad = 0
    for k in F0:
        a, b = F0[k][:, keep], F1[k][:, keep]
        same = np.array_equal(a, b, equal_nan=True)
        if not same:
            bad += 1
        print(("OK  " if same else "DIFF"), k, "finite", float(np.isfinite(a).mean()).__round__(3))
    after = [k for k in F0 if not np.array_equal(F0[k][:, ~keep], F1[k][:, ~keep], equal_nan=True)]
    print("после t0 меняются (контроль, что порча видна):", len(after), "из", len(F0))
    print("RESULT", "FAIL" if bad else "PASS", "ts", len(ts), "coins", len(sy), "t0_idx", t0)
    return bad


if __name__ == "__main__":
    if sys.argv[1] == "--test":
        sys.exit(1 if test(sys.argv[2], sys.argv[3]) else 0)
    main(*sys.argv[1:5])
