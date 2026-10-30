#!/usr/bin/env python3
"""TK-455 / P-TK455 §2–§4, §10: драйвер сделок слоя 3. Сигналы (реальные + контроли C1/C2, sancho_l3_sig) -> сделки по сетке ячеек
-> строки (сигнал × ячейка) с gross/комиссиями/фандингом. Контроль C3 (случайная сторона) — на реальных сигналах.
python tools/compute/sancho_l3_trades.py <csv.gz минуты> <tf_min> <k> <funding_cache_dir> <out.csv.gz> [--anchor]
--anchor — только якорные ячейки (A: N=10; B: E0·S1·T-S·N=10); без — вся сетка осей E/S/T/N.
Допущения реализации (отмечены в отчёте): окно E1 обрезано дедлайном N (заявка после дедлайна бессмысленна); для C3 вход E1 не
считается (уровень L не имеет смысла при случайной стороне); T-S — ближайший по цене подтверждённый (i+k <= t0) свинг по
выгодной стороне, любого возраста. Всё — по данным <= t0; исход читает минуты после."""
import hashlib
import itertools
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sancho import bars, swings  # noqa: E402
from sancho_l3_sig import sig_table  # noqa: E402
from sancho_l3_sim import trade, fill_e1, mirror, gross_bps, STOP, TARGET, DEADLINE  # noqa: E402
from sancho_l3_costs import MAKER_BPS, TAKER_BPS, funding_month, funding_bps  # noqa: E402

SEED = 455
NS = (4, 10, 32)
WS = (4, 10, 32)
MS = (0.5, 1.0, 2.0)
RS = (0.5, 1.0, 2.0)
REASON = {STOP: "stop", TARGET: "target", DEADLINE: "deadline"}


def side_of(ev, dname):
    """ожидаемая сторона: A5–A8 против свипа (H -> шорт), A1/A4 — по пробою (H -> лонг). +1 лонг, -1 шорт."""
    up = dname == "H"
    return (1 if up else -1) if ev in ("A1", "A4") else (-1 if up else 1)


def rnd_side(sym, tf, role, evi, ev, dname, t0):
    h = hashlib.md5(f"{sym}{tf}{role}{evi}{ev}{dname}{t0}".encode()).hexdigest()
    return 1 if (int(h[:8], 16) ^ SEED) & 1 else -1


class Coin:
    def __init__(self, path, tf, k, fcache):
        d = pd.read_csv(path, usecols=[0, 1, 2, 3, 4])
        self.tm, o, h, l, c = (d.iloc[:, i].values for i in range(5))
        self.px = {1: (o.astype(float), h.astype(float), l.astype(float), c.astype(float))}
        self.px[-1] = mirror(*self.px[1], -1)
        self.tf, self.k, self.sym = tf, k, os.path.basename(path).split(".")[0]
        a = bars(path, tf)
        ok = a.ok.values
        bh, bl = a.h.values.astype(float), a.l.values.astype(float)
        self.b0 = int(a.index[0])
        self.bt = a.t.values
        self.swh_i = swings(bh, ok, k)
        self.swl_i = swings(-bl, ok, k)
        self.swh_p, self.swl_p = bh[self.swh_i], bl[self.swl_i]
        months = sorted({pd.Timestamp(t, unit="ms").strftime("%Y-%m") for t in (self.tm[0], self.tm[-1])}
                        | {m.strftime("%Y-%m") for m in pd.period_range(pd.Timestamp(self.tm[0], unit="ms"), pd.Timestamp(self.tm[-1], unit="ms"), freq="M")})
        fr = []
        for ym in months:
            try:
                fr.append(funding_month(self.sym, ym, fcache))
            except Exception as e:  # нет файла месяца (до листинга/позже) — пишется в воронку
                print("funding miss", self.sym, ym, type(e).__name__, file=sys.stderr)
        f = pd.concat(fr) if fr else pd.DataFrame({"t": [], "r": []})
        self.ft, self.fr = f.t.values, f.r.values

    def target_s(self, d, t0, entry_abs):
        """ближайший по цене подтверждённый на t0 свинг по выгодной стороне; None если нет."""
        if d == 1:
            m = (self.swh_i + self.k <= t0) & (self.swh_p > entry_abs)
            return float(self.swh_p[m].min()) if m.any() else None
        m = (self.swl_i + self.k <= t0) & (self.swl_p < entry_abs)
        return float(self.swl_p[m].max()) if m.any() else None


def run_signal(cn, s, d, form, E, W, S, m, T, r, N, stop_dist_bps=None):
    """одна ячейка; d — сторона сделки. stop_dist_bps — для C3 (расстояние стопа с реальной стороны). Возврат dict либо причина пропуска."""
    tf = cn.tf
    o, h, l, c = cn.px[d]
    t0ms = s["tt0"] + tf * 60000
    e0 = int(np.searchsorted(cn.tm, t0ms))
    last = int(np.searchsorted(cn.tm, s["tt0"] + (N + 1) * tf * 60000)) - 1
    lvl = d * s["L"]
    ext = d * s["ext"]
    # --- вход
    if E == "E0":
        e, ep, kind_in = e0, o[e0], "taker"
        e1 = False
    else:
        hi = min(W, N) * tf  # окно в минутах, обрезано дедлайном
        stop_pre = ext if S == "S1" else None
        if stop_pre is None:
            stop_pre = lvl - m * s["atr_bps"] / 1e4 * abs(lvl)
        fl = fill_e1(o, l, e0, hi, lvl, stop_pre)
        if fl is None:
            return "nofill", None
        e, ep = fl
        kind_in, e1 = "maker", True
    if e > last:
        return "nofill", None
    # --- стоп / цель
    if form == "A":
        stop, tgt = -np.inf, None
        risk = np.nan
    else:
        if stop_dist_bps is not None:
            stop = ep - stop_dist_bps / 1e4 * abs(ep)
        elif S == "S1":
            stop = ext
        else:
            stop = ep - m * s["atr_bps"] / 1e4 * abs(ep)
        if not stop < ep:
            return "stop_invalid", None
        risk = (ep - stop) / abs(ep) * 1e4
        if T == "TS":
            tp = cn.target_s(d, s["t0"], abs(ep))
            if tp is None:
                return "no_target", None
            tgt = d * tp
            if not tgt > ep:
                return "no_target", None
        else:
            tgt = ep + r * (ep - stop)
    reason, mx, xp = trade(o, h, l, c, e, last, ep, stop, tgt, e1=e1)
    return finish(cn, d, e, ep, reason, mx, xp, kind_in, risk), dict(e=e, e0=e0, ep=ep, reason=reason, mx=mx, last=last,
                                                                     kind_in=kind_in, risk=risk, e1=e1, c=c, xp=xp, t0ms_s=s["tt0"])


def finish(cn, d, e, ep, reason, mx, xp, kind_in, risk):
    tf = cn.tf
    g = gross_bps(ep, xp)
    kind_out = "maker" if reason == TARGET else "taker"
    legs = [kind_in, kind_out]
    fee_mix = sum(MAKER_BPS if x == "maker" else TAKER_BPS for x in legs)
    fund = funding_bps(cn.ft, cn.fr, int(cn.tm[e]), int(cn.tm[mx]) + 60000, d)
    return dict(reason=REASON[reason], gross=g, fee_mix=fee_mix, fee_mk=2 * MAKER_BPS, fee_tk=2 * TAKER_BPS, fund=fund,
                risk=risk, bars=(mx - e + 1) / tf)


def derive_n(cn, d, big, N, W):
    """тот же путь при меньшем дедлайне N: сделка причинна, поэтому выход до last_N не меняется; позже — дедлайн по close last_N.
    E1: окно заявки min(W,N) — заполнение позже окна = nofill. Точно, не приближение (сверка derive vs прямой счёт в проверке)."""
    tf = cn.tf
    last = int(np.searchsorted(cn.tm, big["t0ms_s"] + (N + 1) * tf * 60000)) - 1
    e = big["e"]
    if big["e1"] and e - big["e0"] >= min(W, N) * tf:
        return "nofill"
    if e > last:
        return "nofill"
    if big["reason"] != DEADLINE and big["mx"] <= last:
        return finish(cn, d, e, big["ep"], big["reason"], big["mx"], big["xp"], big["kind_in"], big["risk"])
    return finish(cn, d, e, big["ep"], DEADLINE, last, big["c"][last], big["kind_in"], big["risk"])


def cells(ev, anchor):
    if ev in ("A1", "A4"):  # только форма A
        return [("A", "E0", 0, None, 0, None, None, n) for n in ((10,) if anchor else NS)]
    out = [("A", "E0", 0, None, 0, None, None, n) for n in ((10,) if anchor else NS)]
    if anchor:
        return out + [("B", "E0", 0, "S1", 0, "TS", None, 10)]
    Es = [("E0", 0)] + [("E1", w) for w in WS]
    Ss = [("S1", 0)] + [("S2", x) for x in MS]
    Ts = [("TS", None)] + [("TR", x) for x in RS]
    for (E, W), (S, m), (T, r), n in itertools.product(Es, Ss, Ts, NS):
        out.append(("B", E, W, S, m, T, r, n))
    return out


REASONS = ("stop", "target", "deadline", "nofill", "stop_invalid", "no_target")
AGG_COLS = ["n_att", "n", "gross", "fee_mix", "fee_mk", "fee_tk", "fund", "gross2", "win_mix", "win_mk", "win_tk"] + ["r_" + x for x in REASONS]
KEYC = ["ev", "dir", "role", "c3", "form", "E", "W", "S", "m", "T", "r", "N", "year"]


def is_anchor(form, E, S, T, N):
    return N == 10 and E == "E0" and (form == "A" or (S == "S1" and T == "TS"))


def acc(agg, key, res):
    """агрегат (не строки): иначе 22 млн строк на монету-ТФ — память (OOM 30.10). Сумма net считается из gross/fee/fund."""
    a = agg.get(key)
    if a is None:
        a = agg[key] = [0.0] * len(AGG_COLS)
    a[0] += 1
    if isinstance(res, str):
        a[11 + REASONS.index(res)] += 1
        return
    a[1] += 1
    g, fm, fk, ft, fu = res["gross"], res["fee_mix"], res["fee_mk"], res["fee_tk"], res["fund"]
    a[2] += g; a[3] += fm; a[4] += fk; a[5] += ft; a[6] += fu; a[7] += g * g
    a[8] += g - fm - fu > 0
    a[9] += g - fk - fu > 0
    a[10] += g - ft - fu > 0
    a[11 + REASONS.index(res["reason"])] += 1


def run(path, tf, k, fcache, anchor):
    """возврат (строки якорных ячеек по сигналам, агрегат сетки по (ev,dir,role,c3,ячейка,год))."""
    cn = Coin(path, tf, k, fcache)
    sg = sig_table(path, tf, k)
    rows, agg = [], {}
    for s in sg.to_dict("records"):
        d = side_of(s["ev"], s["dir"])
        year = pd.Timestamp(s["tt0"], unit="ms").year
        groups = {}
        for cell in cells(s["ev"], anchor):
            groups.setdefault(cell[:7], []).append(cell[7])
        for (form, E, W, S, m, T, r), ns in groups.items():
            nmax = max(ns)
            res_big, big = run_signal(cn, s, d, form, E, W, S, m, T, r, nmax)
            for n in ns:
                if isinstance(res_big, str):
                    res = res_big
                else:
                    res = res_big if n == nmax else derive_n(cn, d, big, n, W)
                key = (s["ev"], s["dir"], s["role"], 0, form, E, W, S, m, T, r, n, year)
                acc(agg, key, res)
                keep = is_anchor(form, E, S, T, n)
                if keep:
                    base = dict(sym=cn.sym, tf=tf, role=s["role"], evi=s["evi"], ev=s["ev"], dir=s["dir"], t0=s["tt0"], side=d,
                                form=form, E=E, W=W, S=S, m=m, T=T, r=r, N=n, c3=0)
                    rows.append(dict(**base, reason=res) if isinstance(res, str) else dict(**base, **res))
                if s["role"] == "real" and E == "E0" and not isinstance(res, str):  # C3: случайная сторона, стоп того же расстояния
                    d3 = rnd_side(cn.sym, tf, "real", s["evi"], s["ev"], s["dir"], s["tt0"])
                    sd = res["risk"] if form == "B" else None
                    if form == "B" and np.isnan(sd):
                        continue
                    r3 = run_signal(cn, s, d3, form, E, W, S, m, T, r, n, stop_dist_bps=sd)[0] if (d3 != d or form == "B") else res
                    acc(agg, (s["ev"], s["dir"], "C3", 1, form, E, W, S, m, T, r, n, year), r3)
                    if keep:
                        k3 = dict(base, side=d3, c3=1, role="C3")
                        rows.append(dict(**k3, reason=r3) if isinstance(r3, str) else dict(**k3, **r3))
    ag = pd.DataFrame([list(k) + v for k, v in agg.items()], columns=KEYC + AGG_COLS)
    return pd.DataFrame(rows), ag


if __name__ == "__main__":
    path, tf, k, fc, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5]
    df, ag = run(path, tf, k, fc, "--anchor" in sys.argv)
    df.to_csv(out, index=False, compression="gzip")
    ag.to_csv(out.replace(".csv.gz", "-agg.csv.gz"), index=False, compression="gzip")
    print(out, len(df), len(ag), ag.n_att.sum(), int(ag.n.sum()))
