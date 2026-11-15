"""Отчёт docs/findings/arb-basis-split-2026-11-15.md и рисунки: все числа считаются здесь по pkl basis_split_run.py (книги, сутки, времена жизни) и basis_split_slow.py
(лестница, спектр, волна, фандинг, хвост). Запуск: python3 basis_split_report.py <full.pkl> <slow.pkl> <check.md> <gaps.txt> <каталог выхода>.
Печатает таблицы блоками; текст отчёта собирается в basis_split_text.py из этих блоков."""
import json
import pickle
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import basis_split as bs  # noqa: E402
import basis_split_tools as bt  # noqa: E402

GRP = ("ok", "bx")
GNAME = {"ok": "Binance/Bybit/OKX (3 пары бирж)", "bx": "пары с BingX (3 пары бирж)"}
CLASS_BIN = [0, 8, 13, 17, 20, 24, 27, bs.NB]
CLASS_NAMES = ["< 56 мс", "56 мс – 1 с", "1 – 10 с", "10 – 56 с", "56 с – 9,4 мин", "9,4 – 53 мин", "≥ 53 мин"]


def tz(ms, fmt="%d.%m %H:%M"):
    return (datetime.fromtimestamp(ms / 1000, timezone.utc) + timedelta(hours=4)).strftime(fmt)


def f3(v, n=3):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    return f"{v:.{n}f}".replace(".", ",")


def pct(v, n=1):
    if v is None or not np.isfinite(v):
        return "—"
    return f"{100 * v:.{n}f}".replace(".", ",") + " %"


def table(head, rows, align=None):
    out = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] + ["---:"] * (len(head) - 1) if align is None else align) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(out)


class Data:
    def __init__(self, full, slow):
        self.d = pickle.load(open(full, "rb"))
        s = pickle.load(open(slow, "rb"))
        self.S, self.meta = s["pairs"], s["meta"]
        self.P = self.d["pairs"]
        self.days = self.d["days"]
        self.nday = len(self.days) - 1
        self.G = {k: float(np.nanmedian(f["gap_d"])) for k, f in self.P.items()}
        self.keys = {g: [k for k in self.P if bt.group_of(k) == g and self.S[k]["cover"] > 0.9] for g in GRP}
        self.keys_all = {g: [k for k in self.P if bt.group_of(k) == g] for g in GRP}
        self._lt = {}

    def day_label(self, i):
        a, b = self.days[i], self.days[i + 1]
        return f"{tz(a, '%d.%m')} ({tz(a, '%H:%M')}–{tz(b, '%H:%M') if tz(b, '%H:%M') != '00:00' else '24:00'}, {(b - a) / 3.6e6:.1f} ч)"

    def lt(self, grp, kind, ref, x):
        """Закрытые и не вышедшие эпизоды по суткам: (закрытые (nday, NB), открытые (nday, NB))."""
        key = (grp, kind, ref, x)
        if key not in self._lt:
            r, xi, k = self.d["refs"].index(ref), self.d["xs"].index(x), self.d["kinds"].index(kind)
            c = sum(f["lt"][r, xi, k] for kk, f in self.P.items() if bt.group_of(kk) == grp).astype(float)
            o = sum(f["lt_open"][r, xi, k] for kk, f in self.P.items() if bt.group_of(kk) == grp).astype(float)
            self._lt[key] = (c, o)
        return self._lt[key]


LO = np.r_[1.0, bs.LT_EDGES]
HI = np.r_[bs.LT_EDGES, bs.LT_EDGES[-1] * 10 ** 0.25]
MID = np.sqrt(LO * HI)
MID[0] = 0.5


def class_shares(c, o):
    """Доля эпизодов и доля времени в эпизодах по классам времени жизни (закрытые + не вышедшие по прошедшему времени)."""
    n = (c + o).sum(axis=0)
    tw = n * MID
    out_n, out_t = [], []
    for a, b in zip(CLASS_BIN[:-1], CLASS_BIN[1:]):
        out_n.append(n[a:b].sum() / max(n.sum(), 1))
        out_t.append(tw[a:b].sum() / max(tw.sum(), 1))
    return np.array(out_n), np.array(out_t), float(n.sum())


def sec_lifetimes(D):
    out = []
    rows = []
    for g in GRP:
        for kind in ("exec", "mid"):
            for ref in ("w60", "day"):
                for x in (0.1, 0.3, 1.0):
                    c, o = D.lt(g, kind, ref, x)
                    sn, st, n = class_shares(c, o)
                    rows.append([("ok" if g == "ok" else "BingX"), kind, ref, f3(x, 1), f"{int(n):,}".replace(",", " ")] + [pct(v, 1) for v in sn])
    out.append("### Доля эпизодов по классам времени жизни\n\n" + table(["пары", "вид", "опора", "x, %", "эпизодов"] + CLASS_NAMES, rows))
    rows = []
    for g in GRP:
        for kind in ("exec", "mid"):
            for ref in ("w60", "day"):
                for x in (0.1, 0.3, 1.0):
                    c, o = D.lt(g, kind, ref, x)
                    sn, st, n = class_shares(c, o)
                    rows.append([("ok" if g == "ok" else "BingX"), kind, ref, f3(x, 1)] + [pct(v, 1) for v in st])
    out.append("### Доля времени, проведённого в отклонении, по классам времени жизни эпизода\n\n" + table(["пары", "вид", "опора", "x, %"] + CLASS_NAMES, rows))
    return "\n\n".join(out)


def sec_lifetime_hist(D, grp="ok", kind="exec", ref="w60", x=0.1):
    c, o = D.lt(grp, kind, ref, x)
    tot = c.sum(axis=0) + o.sum(axis=0)
    sur = 1 - np.cumsum(tot) / tot.sum()
    rows = []
    for b in range(bs.NB):
        lo, hi = bt.lt_label(b)
        if tot[b] == 0 and (b == 0 or sur[b] == 0):
            continue
        per_day = [100 * (c[d, b] + o[d, b]) / max((c[d] + o[d]).sum(), 1) for d in range(D.nday)]
        rows.append([f"{bt.fmt_ms(lo)} – {bt.fmt_ms(hi)}", f"{int(tot[b]):,}".replace(",", " "), f3(100 * tot[b] / tot.sum(), 2), f3(100 * sur[b], 3)] + [f3(v, 2) for v in per_day])
    return table(["время жизни", "эпизодов", "доля, %", "дольше этого, %"] + [f"доля, % · {tz(D.days[i], '%d.%m')}" for i in range(D.nday)], rows)


def valleys(D):
    """Впадины и пики сглаженной (по корзинам ±1) плотности времён жизни по всем сочетаниям (вид, опора, x), по суткам и сразу по всему окну."""
    rows, summary = [], {}
    for g in GRP:
        cnt = {"valley": 0, "none": 0, "total": 0}
        pos = {}
        for kind in D.d["kinds"]:
            for ref in D.d["refs"]:
                for x in D.d["xs"]:
                    c, o = D.lt(g, kind, ref, x)
                    h = (c + o).sum(axis=0)
                    if h.sum() < 2000:
                        continue
                    h = h / h.sum()
                    pk, vl, dp = bt.modes_valleys(h, 1)
                    cnt["total"] += 1
                    v1 = [(v, p) for v, p in zip(vl, dp) if bt.lt_label(v)[1] <= 320 and p < 0.95]       # первая впадина до ≈ 0,3 с
                    if v1:
                        cnt["valley"] += 1
                        pos[v1[0][0]] = pos.get(v1[0][0], 0) + 1
                    else:
                        cnt["none"] += 1
        summary[g] = (cnt, pos)
    return summary


def sec_levels(D):
    rows_q, out = [], {}
    for g in GRP:
        ks = D.keys[g]
        dl_day = np.full((len(ks), D.nday), np.nan)
        dd = np.full((len(ks), 2), np.nan)
        lv, gg, mx, lvabs = [], [], [], []
        Ld = np.array([D.P[k]["ref_day"] for k in ks])
        L = np.array([D.P[k]["level_all"] for k in ks], float)
        Gk = np.array([D.G[k] for k in ks])
        dl_day = np.abs(Ld - L[:, None])
        dd[:, 0] = np.abs(Ld[:, 1] - Ld[:, 0])
        dd[:, 1] = np.abs(Ld[:, 2] - Ld[:, 1])
        out[g] = {"L": L, "Ld": Ld, "G": Gk, "dl_day": dl_day, "dd": dd}
    return out


def sec_level_text(D, LV):
    rows = []
    for g in GRP:
        a = LV[g]
        mx = np.nanmax(a["dl_day"], axis=1)
        for name, v in (("|уровень окна|", np.abs(a["L"])), ("max по суткам |L_сутки − L_окна|", mx), ("|L_2 − L_1|", a["dd"][:, 0]), ("|L_3 − L_2|", a["dd"][:, 1])):
            q = np.percentile(v, [25, 50, 75, 90, 99])
            rows.append([("ok" if g == "ok" else "BingX"), name] + [f3(t, 3) for t in q])
    t1 = table(["пары", "величина, %", "p25", "p50", "p75", "p90", "p99"], rows)
    rows = []
    for g in GRP:
        a = LV[g]
        mx = np.nanmax(a["dl_day"], axis=1)
        G = a["G"]
        sh = lambda m: pct(float(np.mean(m)), 1)  # noqa: E731
        rows.append([("ok" if g == "ok" else "BingX"), len(mx), sh(mx <= 0.02), sh(mx <= 0.05), sh(mx <= 0.1), sh(mx <= 0.5 * G), sh(mx <= G), sh(np.abs(a["L"]) > G)])
    t2 = table(["пары", "пар", "сдвиг по суткам ≤ 0,02 %", "≤ 0,05 %", "≤ 0,1 %", "≤ ½ спреда", "≤ спреда", "уровень окна > спреда"], rows)
    return t1, t2


def ladder_tables(D):
    names = bt.ladder_names()
    rows, rows_big, rows_day = [], [], []
    for g in GRP:
        ks = D.keys[g]
        std = np.array([D.S[k]["lad_std"] for k in ks])
        tot = np.array([D.S[k]["tot_std"] for k in ks])
        G = np.array([D.G[k] for k in ks])
        big = tot > G
        rows.append([("ok" if g == "ok" else "BingX"), len(ks)] + [f3(v * 100, 2) for v in np.nanmedian(std, axis=0)])
        rows_big.append([("ok" if g == "ok" else "BingX"), int(big.sum())] + [f3(v, 2) for v in np.nanmedian(std[big] / G[big, None], axis=0)])
        var = std ** 2
        sh = var / np.nansum(var, axis=1, keepdims=True)
        rows_day.append([("ok" if g == "ok" else "BingX"), len(ks)] + [pct(v, 0) for v in np.nanmedian(sh, axis=0)])
    head = ["пары", "пар"] + names
    return table(head, rows), table(head, rows_big), table(head, rows_day)


def sf_tables(D):
    out = []
    for g in GRP:
        ks = D.keys[g]
        A = np.array([D.P[k]["sf"][1, :, 0] for k in ks])
        A9 = np.array([D.P[k]["sf"][1, :, 1] for k in ks])
        Gk = np.array([D.G[k] for k in ks])
        ok = np.isfinite(A).sum(axis=1) >= len(bs.LAGS_S) - 4
        rows = []
        for li in (0, 3, 6, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27):
            L = bs.LAGS_S[li]
            lab = f"{L} с" if L < 60 else (f"{L // 60} мин" if L < 3600 else f"{L / 3600:.3g} ч".replace(".", ","))
            v = A[ok, li]
            w = A9[ok, li]
            rows.append([lab, f3(np.nanpercentile(v, 25), 4), f3(np.nanmedian(v), 4), f3(np.nanpercentile(v, 75), 4), f3(np.nanmedian(w), 4), f3(np.nanmedian(v / Gk[ok]), 3)])
        out.append(f"**{GNAME[g]}**, сутки {D.day_label(1)}, пар {int(ok.sum())}\n\n" + table(["сдвиг по времени τ", "p50 |Δ| — p25 пар, %", "p50 пар", "p75 пар", "p90 |Δ| — p50 пар, %", "p50 |Δ| / G, p50 пар"], rows))
    return "\n\n".join(out)


def crossover_tables(D):
    rows = []
    for g in GRP:
        ks = D.keys[g]
        cr = np.array([D.S[k]["cross_d"] for k in ks])
        for d in range(D.nday):
            v = cr[:, d][np.isfinite(cr[:, d])]
            rows.append([("ok" if g == "ok" else "BingX"), D.day_label(d)] + [f"{x:.0f}" for x in np.percentile(v, [10, 25, 50, 75, 90])])
        for a, b in ((0, 1), (1, 2)):
            ok = np.isfinite(cr[:, a]) & np.isfinite(cr[:, b])
            r = np.maximum(cr[ok, a], cr[ok, b]) / np.minimum(cr[ok, a], cr[ok, b])
            rows.append([("ok" if g == "ok" else "BingX"), f"отношение между сутками {a + 1}–{b + 1} (p50 / доля ≤ 2×)", f3(np.median(r), 2), pct(float(np.mean(r <= 2)), 0), "", "", ""])
    return table(["пары", "сутки", "p10", "p25", "p50", "p75", "p90"], rows)


def wave_by_day(D, wi_name="60"):
    wi = list(bt.LADDER_W).index(int(wi_name))
    rows = []
    for g in GRP:
        ks = D.keys[g]
        A = np.array([D.S[k]["wave_std_d"][:, wi] for k in ks])
        F = np.array([D.S[k]["fast_std_d"][:, wi] for k in ks])
        Gk = np.array([D.G[k] for k in ks])
        for d in range(D.nday):
            rows.append([("ok" if g == "ok" else "BingX"), D.day_label(d), f3(np.nanmedian(A[:, d]) * 100, 2), f3(np.nanmedian(F[:, d]) * 100, 2), f3(np.nanmedian(A[:, d] / Gk), 3), f3(np.nanmedian(F[:, d] / Gk), 3),
                         pct(float(np.nanmean(A[:, d] > F[:, d])), 0), pct(float(np.nanmean(A[:, d] >= 0.5 * Gk)), 1)])
    return table(["пары", "сутки", "медленная часть (ref_60 − сутки), σ, bps", "быстрая часть (минута − ref_60), σ, bps", "медленная / G", "быстрая / G", "пар с медленной > быстрой", "пар с медленной ≥ ½ G"], rows)


def amp_repro(D):
    rows = []
    from scipy.stats import spearmanr
    for g in GRP:
        ks = D.keys[g]
        for w in (20, 60, 240):
            wi = list(bt.LADDER_W).index(w)
            A = np.array([D.S[k]["wave_std_d"][:, wi] for k in ks])
            Gk = np.array([D.G[k] for k in ks])
            for a, b in ((0, 1), (1, 2)):
                ok = np.isfinite(A[:, a]) & np.isfinite(A[:, b]) & (A[:, a] > 0) & (A[:, b] > 0)
                rho = spearmanr(A[ok, a], A[ok, b])[0]
                ratio = np.maximum(A[ok, a], A[ok, b]) / np.minimum(A[ok, a], A[ok, b])
                big = ok & (np.maximum(A[:, a], A[:, b]) >= 0.5 * Gk)
                rb = np.maximum(A[big, a], A[big, b]) / np.minimum(A[big, a], A[big, b])
                rows.append([("ok" if g == "ok" else "BingX"), f"{w} мин", f"сутки {a + 1}–{b + 1}", f3(rho, 2), f3(float(np.median(ratio)), 2), pct(float(np.mean(ratio <= 2)), 0), int(big.sum()), f3(float(np.median(rb)), 2), pct(float(np.mean(rb <= 2)), 0)])
    return table(["пары", "окно", "сутки", "ρ Спирмена по парам", "отношение амплитуд, p50", "доля ≤ 2×", "пар с амплитудой ≥ ½ G", "отношение, p50", "доля ≤ 2×"], rows)


def clock_repro(D):
    def corr(h, a, b, sh):
        x, y = h[a], np.roll(h[b], sh)
        m = np.isfinite(x) & np.isfinite(y)
        if m.sum() < 8 or x[m].std() == 0 or y[m].std() == 0:
            return np.nan
        return float(np.corrcoef(x[m], y[m])[0, 1])

    rows = []
    for g in GRP:
        ks = D.keys[g]
        sel = {"все пары": ks, "медленная часть ≥ ½ G": [k for k in ks if D.S[k]["wave_std_d"][1, list(bt.LADDER_W).index(60)] >= 0.5 * D.G[k]]}
        for name, kk in sel.items():
            for a, b in ((0, 1), (1, 2)):
                cells = []
                for sh in (0, 3, 6, 9, 12):
                    v = np.array([corr(D.S[k]["hour"], a, b, sh) for k in kk])
                    cells.append(f"{np.nanmedian(v):+.2f} / {100 * np.nanmean(v > 0.5):.0f} %")
                rows.append([("ok" if g == "ok" else "BingX"), name, len(kk), f"сутки {a + 1}–{b + 1}"] + cells)
    return table(["пары", "набор", "пар", "сутки", "сдвиг 0 ч (по часам): r p50 / доля r > 0,5", "сдвиг 3 ч (контроль)", "6 ч", "9 ч", "12 ч"], rows)


def lines_table(D):
    rows = []
    for g in GRP:
        ks = D.keys[g]
        for p in D.meta["lines"]:
            v = [D.S[k]["lines"][p] for k in ks if D.S[k]["lines"].get(p)]
            if not v:
                continue
            snr = np.array([x["snr"] for x in v if x["snr"] is not None])
            ph = np.array([x["phase"] for x in v])
            R, pv = bt.rayleigh(2 * ph)
            kind = "контроль" if p in (420, 540, 600, 300) else ("8 ч — фандинг" if p == 480 else "")
            rows.append([("ok" if g == "ok" else "BingX"), f"{p / 60:.1f} ч".replace(".", ","), kind, len(v), f3(float(np.median(snr)), 2), pct(float((snr > 3).mean()), 1), pct(float((snr > 10).mean()), 1), f3(R, 3), f"{pv:.2g}"])
    return table(["пары", "период", "тип", "пар", "p50 выраженности линии", "доля > 3", "доля > 10", "R (удвоенная фаза)", "p Рэлея"], rows)


def peaks_table(D):
    rows = []
    edges = [60, 120, 180, 240, 360, 480, 600, 800, 1000]
    for g in GRP:
        ks = D.keys[g]
        per = np.array([D.S[k]["peak"][0] for k in ks if D.S[k].get("peak")])
        h = np.histogram(per, bins=edges)[0]
        rows.append([("ok" if g == "ok" else "BingX"), len(per)] + [pct(v / len(per), 0) for v in h])
    return table(["пары", "пар"] + [f"{a // 60}–{b // 60}".replace(".", ",") + " ч" if a % 60 == 0 and b % 60 == 0 else f"{a / 60:.1f}–{b / 60:.1f} ч".replace(".", ",") for a, b in zip(edges[:-1], edges[1:])], rows)


def acf_band_tables(D):
    rows_a, rows_b = [], []
    for g in GRP:
        ks = D.keys[g]
        A = np.array([D.S[k]["acf"] for k in ks if D.S[k].get("acf") is not None])
        rows_a.append([("ok" if g == "ok" else "BingX"), len(A)] + [f3(v, 2) for v in np.nanmedian(A, axis=0)])
        B = np.array([D.S[k]["band"] for k in ks if D.S[k].get("band") is not None])
        rows_b.append([("ok" if g == "ok" else "BingX"), len(B)] + [pct(v, 0) for v in np.nanmedian(B, axis=0)])
    ha = ["пары", "пар"] + [f"{L} мин" for L in bt.ACF_LAGS]
    hb = ["пары", "пар"] + [f"{a}–{b} мин" for a, b in zip(bt.BAND_EDGES[:-1], bt.BAND_EDGES[1:])]
    return table(ha, rows_a), table(hb, rows_b)


def funding_table(D):
    off = D.meta["off"]
    rows = []
    for g in GRP:
        ks = [k for k in D.keys[g] if "act_fund" in D.S[k]]
        AF = np.array([D.S[k]["act_fund"] for k in ks])
        AP = np.array([D.S[k]["act_plac"] for k in ks])
        mf = np.nanmedian(AF, axis=0)               # события × корзины
        mp = np.nanmedian(AP, axis=0)
        for j in (6, 9, 11, 12, 13, 14, 15, 18, 21):
            f_ = mf[:, j]
            p_ = mp[:, j]
            z = (np.nanmean(f_) - np.nanmean(p_)) / (np.nanstd(p_) / np.sqrt(len(f_)) if np.nanstd(p_) > 0 else np.nan)
            rows.append([("ok" if g == "ok" else "BingX"), f"{off[j]:+d} … {off[j] + 10:+d}", f3(float(np.nanmean(f_)), 3), f3(float(np.nanmean(p_)), 3), f3(float(np.nanstd(p_)), 3), f"{z:+.1f}"])
    return table(["пары", "минуты от момента 00/08/16 UTC", "активность на фандинге (среднее по 6 моментам)", "в контрольные моменты (среднее по 19)", "σ контрольных", "z"], rows), (len(mf), len(mp))


def tail_tables(D):
    tw, ta, tg = D.meta["tail_w"], D.meta["tail_abs"], D.meta["tail_g"]
    rows_a, rows_g = [], []
    for g in GRP:
        ks = D.keys[g]
        T = sum(D.S[k]["tail"].astype(np.int64) for k in ks)
        N = sum(D.S[k]["tail_n"].astype(np.int64) for k in ks)
        for wi, w in enumerate(tw):
            if w not in (20, 60, 240):
                continue
            for ki, kn in enumerate(D.meta["tail_kinds"]):
                n = N[:, wi, ki].sum()
                ha = T[:, wi, ki, 0, :].sum(axis=0)
                sv = 1 - np.cumsum(ha) / ha.sum()                         # среди минут с положительным отклонением
                pos = ha.sum() / n
                pts = [0.05, 0.1, 0.2, 0.3, 0.5, 1.0]
                vals = [pos * sv[int(np.searchsorted(ta, a, "right")) - 1] for a in pts]
                rows_a.append([("ok" if g == "ok" else "BingX"), f"{w} мин", {"sell": "Sell − опора", "buy": "опора − Buy", "mid": "|середина − опора|"}[kn], f"{int(n):,}".replace(",", " ")] + [pct(v, 2) for v in vals])
                hg = T[:, wi, ki, 1, :len(tg) + 1].sum(axis=0)
                sg = 1 - np.cumsum(hg) / max(hg.sum(), 1)
                posg = hg.sum() / n
                ptg = [0.25, 0.5, 1, 2, 3, 5, 10]
                valg = [posg * sg[int(np.searchsorted(tg, a, "right")) - 1] for a in ptg]
                rows_g.append([("ok" if g == "ok" else "BingX"), f"{w} мин", {"sell": "Sell − опора", "buy": "опора − Buy", "mid": "|середина − опора|"}[kn]] + [pct(v, 2) for v in valg])
    ta_ = table(["пары", "опора", "что", "минут с данными", "≥ 0,05 %", "≥ 0,1 %", "≥ 0,2 %", "≥ 0,3 %", "≥ 0,5 %", "≥ 1 %"], rows_a)
    tg_ = table(["пары", "опора", "что", "≥ ¼ G", "≥ ½ G", "≥ 1 G", "≥ 2 G", "≥ 3 G", "≥ 5 G", "≥ 10 G"], rows_g)
    return ta_, tg_


def episodes_by_day(D):
    rows = []
    for g in GRP:
        ks = D.keys_all[g]
        for ref in ("w60", "day"):
            for x in (0.3, 0.5, 1.0):
                r, xi, k = D.d["refs"].index(ref), D.d["xs"].index(x), D.d["kinds"].index("exec")
                per = np.array([D.P[kk]["lt"][r, xi, k].sum(axis=1) + D.P[kk]["lt_open"][r, xi, k].sum(axis=1) for kk in ks]).astype(float)   # пары × сутки
                hours = np.array([(D.days[i + 1] - D.days[i]) / 3.6e6 for i in range(D.nday)])
                tot = per.sum(axis=0)
                rows.append([("ok" if g == "ok" else "BingX"), ref, f3(x, 1)] + [f"{int(t):,}".replace(",", " ") for t in tot] + [f3(float(np.median(per.sum(axis=1) / (hours.sum() / 24))), 1), pct(float((per.sum(axis=1) > 0).mean()), 0)])
    return table(["пары", "опора", "x, %"] + [f"эпизодов · {tz(D.days[i], '%d.%m')}" for i in range(D.nday)] + ["на пару за сутки, p50", "пар с хотя бы одним"], rows)


def clean_split(D):
    """Доля пар с чистым разделением (критерии — решение исполнителя, привязаны к измеренному спреду G пары и к самим амплитудам)."""
    wi = list(bt.LADDER_W).index(60)
    res = {}
    for g in GRP:
        ks = D.keys[g]
        n = len(ks)
        G = np.array([D.G[k] for k in ks])
        L = np.array([D.P[k]["level_all"] for k in ks], float)
        Ld = np.array([D.P[k]["ref_day"] for k in ks])
        dL = np.nanmax(np.abs(Ld - L[:, None]), axis=1)
        A = np.array([D.S[k]["wave_std_d"][:, wi] for k in ks])        # сутки
        F = np.array([D.S[k]["fast_std_d"][:, wi] for k in ks])
        tot = np.array([D.S[k]["tot_std"] for k in ks])
        Amin, Amax, Amed = np.nanmin(A, axis=1), np.nanmax(A, axis=1), np.nanmedian(A, axis=1)
        Fmed = np.nanmedian(F, axis=1)
        out = {"n": n}
        out["quiet"] = float(np.mean(tot < 0.5 * G))
        out["T1"] = float(np.mean(dL <= G))
        out["T2"] = float(np.mean(Amin >= 0.5 * G))
        out["T3"] = float(np.mean(np.nanmin(A / F, axis=1) >= 2))
        out["T4"] = float(np.mean(Amax / np.maximum(Amin, 1e-12) <= 2))
        out["T5"] = float(np.mean(dL <= Amed))
        for name, (k1, k2, k3, k4) in {"base": (1.0, 0.5, 2.0, 2.0), "loose": (2.0, 0.25, 1.0, 3.0), "strict": (0.5, 1.0, 3.0, 1.5)}.items():
            ok = (dL <= k1 * G) & (Amin >= k2 * G) & (np.nanmin(A / F, axis=1) >= k3) & (Amax / np.maximum(Amin, 1e-12) <= k4) & (dL <= Amed)
            out[name] = float(ok.mean())
            out[name + "_n"] = int(ok.sum())
        # по суткам: доля пар, у которых критерий выполнен на каждые сутки отдельно (уровень сдвинут от окна ≤ G, медленная ≥ ½ G, медленная ≥ 2× быстрой)
        per_day = []
        for d in range(D.nday):
            okd = (np.abs(Ld[:, d] - L) <= G) & (A[:, d] >= 0.5 * G) & (A[:, d] >= 2 * F[:, d])
            per_day.append(float(np.nanmean(okd)))
        out["per_day"] = per_day
        res[g] = out
    return res


def main(full, slow, check, gaps, outdir):
    D = Data(full, slow)
    Path(outdir).mkdir(parents=True, exist_ok=True)
    blocks = {}
    blocks["lifetimes_classes"] = sec_lifetimes(D)
    blocks["lifetime_hist_ok_exec_w60_01"] = sec_lifetime_hist(D, "ok", "exec", "w60", 0.1)
    blocks["lifetime_hist_ok_exec_day_03"] = sec_lifetime_hist(D, "ok", "exec", "day", 0.3)
    blocks["lifetime_hist_bx_exec_w60_01"] = sec_lifetime_hist(D, "bx", "exec", "w60", 0.1)
    blocks["valleys"] = valleys(D)
    LV = sec_levels(D)
    blocks["level_q"], blocks["level_share"] = sec_level_text(D, LV)
    blocks["ladder"], blocks["ladder_big"], blocks["ladder_share"] = ladder_tables(D)
    blocks["sf"] = sf_tables(D)
    blocks["cross"] = crossover_tables(D)
    blocks["wave_day"] = wave_by_day(D)
    blocks["amp_repro"] = amp_repro(D)
    blocks["clock_repro"] = clock_repro(D)
    blocks["lines"] = lines_table(D)
    blocks["peaks"] = peaks_table(D)
    blocks["acf"], blocks["band"] = acf_band_tables(D)
    blocks["funding"], blocks["funding_n"] = funding_table(D)
    blocks["tail_pct"], blocks["tail_g"] = tail_tables(D)
    blocks["episodes_day"] = episodes_by_day(D)
    blocks["clean"] = clean_split(D)
    pickle.dump({"blocks": blocks, "LV": {g: {k: v for k, v in a.items()} for g, a in LV.items()}}, open(Path(outdir, "blocks.pkl"), "wb"))
    for k, v in blocks.items():
        print(f"\n===== {k} =====")
        print(v if not isinstance(v, dict) else json.dumps(v, ensure_ascii=False, default=str, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:6])
