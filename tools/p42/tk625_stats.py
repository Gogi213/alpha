#!/usr/bin/env python3
"""П-42 §13.32 (TK-625): статистика после PASS — таблица ступени 1 (корзины признака на сделках баз), клетки `n_ok ≥ c`, ворота G_625 (п. 5), повтор на o566, правило ответа (п. 7).
Чистые функции над рядами сделок (`tk604_stats.Series`): диска и движка не касаются, тесты — `tests/test_tk625.py`. Формулы Δ, бутстрепа, Холма, `robust`, `need_months` — `tk604_stats` без изменений."""
from __future__ import annotations

import dataclasses

import numpy as np

import tk604_stats as st

BUCKETS = ("0", "1", "2–3", "4+", "нет значения")
NEIGHBOUR_PLATEAU_MIN = 2                      # п. 5 (g5): ≥ 2 соседей (оба: m = 0,5 и 2 при тех же D и q)
YEAR_DAYS = 365


def bucket_index(x):
    """0 / 1 / 2–3 / 4+ / нет значения (NaN) — индексы в `BUCKETS`."""
    x = np.asarray(x, dtype=float)
    b = np.full(len(x), 4, dtype=np.int64)
    ok = ~np.isnan(x)
    b[ok & (x == 0)] = 0
    b[ok & (x == 1)] = 1
    b[ok & (x >= 2) & (x <= 3)] = 2
    b[ok & (x >= 4)] = 3
    return b


def sharpe_year(day_sums):
    """Годовой Sharpe суточного ряда Σ $ (все сутки окна, нули в сутки без сделок): среднее / станд. откл. (ddof = 1) × √365; нет разброса — nan."""
    s = np.asarray(day_sums, dtype=float)
    sd = float(s.std(ddof=1)) if len(s) > 1 else 0.0
    return float(s.mean() / sd * np.sqrt(YEAR_DAYS)) if sd > 0 else float("nan")


def slice_stats(day, val, nboot=st.NBOOT):
    """Срез сделок: число, Σ $, $/сделку (без медиан), Sharpe годовой, (сделок, Σ $) по месяцам, 90 % интервал $/сделку (бутстреп по суткам, круговые блоки по 4)."""
    day = np.asarray(day, dtype=np.int64)
    val = np.asarray(val, dtype=float)
    Sd = np.bincount(day, weights=val, minlength=st.NDAYS)
    Nd = np.bincount(day, minlength=st.NDAYS).astype(float)
    n = int(len(day))
    out = dict(n=n, sum=float(val.sum()), mean=float(val.sum() / n) if n else float("nan"), sharpe=sharpe_year(Sd), months=[], lo90=None, hi90=None)
    mon = st.MONTH_OF_DAY[day] if n else np.zeros(0, dtype=np.int64)
    for k in range(len(st.MONTH_LEN)):
        m = mon == k
        out["months"].append((int(m.sum()), float(val[m].sum())))
    if n:
        idx = st.block_idx(st.NDAYS, np.random.default_rng(st.seed_of(Sd, Nd, Sd, Nd)), nboot)
        bS, bN = Sd[idx].sum(axis=1), Nd[idx].sum(axis=1)
        ok = bN > 0
        if ok.mean() >= 0.99:
            lo, hi = np.percentile(bS[ok] / bN[ok], [5, 95])
            out["lo90"], out["hi90"] = float(lo), float(hi)
    return out


def step1_rows(ser, x, nboot=st.NBOOT):
    """{корзина: slice_stats} + "все" (вся база) для ряда `ser` и значений признака `x` (длина = числу сделок; NaN — нет значения)."""
    b = bucket_index(x)
    out = {"все": slice_stats(ser.day, ser.val, nboot)}
    for i, name in enumerate(BUCKETS):
        m = b == i
        out[name] = slice_stats(ser.day[m], ser.val[m], nboot)
    return out


# ---- клетки ------------------------------------------------------------------------------------------------------------------

@dataclasses.dataclass
class Cell:
    cid: str
    m: str                         # "05" / "10" / "20"
    D: int
    q: float
    c: int | None
    trial: bool
    dup_of: str | None             # cid клетки с тем же множеством исключённых / "нет порога"
    series: st.Series | None       # None у дубля и у клетки без порога
    excl: np.ndarray               # булев массив по сделкам базы


@dataclasses.dataclass
class SideCells:
    base: st.Series
    cells: list
    ncoins: int


def _omask(om):
    m = np.zeros(st.NDAYS, dtype=bool)
    m[om] = True
    return m


def cell_row(side, cell, om, nboot=st.NBOOT):
    """Строка клетки на Ω: Δ ± SE, z, p, ΔΣ ± SE_Σ, Σ, сделок, исключено (всего и по месяцам), допуск п. 5, знак по информативным месяцам (g2), (g3), (g4)."""
    base = side.base
    Sc, Nc = st.day_sums(cell.series)
    Sr, Nr = st.day_sums(base)
    mt = st.month_table(Sc, Nc, Sr, Nr, om)
    inom = _omask(om)
    ex = cell.excl & inom[base.day]
    n_ex = int(ex.sum())
    ex_m = {k: int((ex & (st.MONTH_OF_DAY[base.day] == k)).sum()) for k in sorted(set(st.MONTH_OF_DAY[om].tolist()))}
    a1, a2 = bool(st.min_trades_ok(mt)), n_ex >= st.MIN_EXCL
    row = dict(cid=cell.cid, m=cell.m, D=cell.D, q=cell.q, c=cell.c, trial=cell.trial, n_ex=n_ex, ex_month=ex_m, month=mt, a1=a1, a2=a2, mask=ex,
               admitted=False, d=float("nan"), se=float("nan"), z=float("nan"), p=float("nan"), dsum=float("nan"), se_sum=float("nan"), s_c=float(Sc[om].sum()),
               n_c=int(Nc[om].sum()), n_r=int(Nr[om].sum()))
    if Nc[om].sum() <= 0 or Nr[om].sum() <= 0:
        return row
    ds = st.delta_stat(Sc[om], Nc[om], Sr[om], Nr[om], nboot)
    row.update(d=ds.d, se=ds.se, z=ds.z, p=ds.p, dsum=ds.dsum, se_sum=ds.se_sum, s_c=ds.s_c, admitted=bool(a1 and a2))
    row.update(gates_one(side, cell, om, row, ds))
    return row


def gates_one(side, cell, om, row, ds):
    """(g2), (g3), (g4) клетки; (g1) и (g5) — на уровне пакета (`evaluate`)."""
    inf = [k for k, n in row["ex_month"].items() if n >= st.MIN_EXCL_MONTH]
    need = st.need_months(len(inf))
    pos = sum(1 for k in inf if row["month"][k][0] > 0)
    g2 = bool(need is not None and pos >= need)
    g3 = bool(ds.s_c > 0 and ds.dsum >= -1.0 * ds.se_sum)
    ok, dd, dc = st.robust(cell.series, side.base, om, side.ncoins)
    return dict(g2=g2, g2_note="информативных месяцев %d, с Δ > 0: %d, нужно %s; Δ по месяцам %s" % (
        len(inf), pos, need, {st.MONTH_NAMES[k]: round(v[0], 4) for k, v in row["month"].items()}), g3=g3, g4=bool(ok), g4_note="без 2 суток %s, без 2 монет %s" % (dd, dc))


def evaluate(sides, om, nboot=st.NBOOT):
    """Ворота G_625 на Ω (h775): строки всех клеток; Холм по всем пробным клеткам без дублей обеих сторон (g1; m — из п. 11, недопущенные входят с p = 1); плато (g5) по соседям m = 0,5 и 2; прошла = g1…g5.
    sides: {"L": SideCells, "S": SideCells}. Возвращает dict(rows={side: {cid: row}}, holm=dict(m, ps), passed=[(side, cid)])."""
    om = np.asarray(om)
    rows = {}
    for s, sd in sides.items():
        rows[s] = {}
        for c in sd.cells:
            if c.series is not None:
                rows[s][c.cid] = cell_row(sd, c, om, nboot)
    trial = [(s, c.cid) for s, sd in sides.items() for c in sd.cells if c.trial and c.series is not None]       # m = пробные клетки без дублей (п. 11), в том числе недопущенные
    ps = [rows[s][cid]["p"] if rows[s][cid]["admitted"] else 1.0 for s, cid in trial]                      # недопущенная клетка в Холм входит с p = 1 (g1 не проходит)
    rej = st.holm_reject(ps, 0.05) if ps else []
    for (s, cid), r in zip(trial, rej):
        rows[s][cid]["g1"] = bool(r and rows[s][cid]["admitted"] and rows[s][cid]["d"] > 0)
    passed = []
    for s, sd in sides.items():
        by = {c.cid: c for c in sd.cells}
        for c in sd.cells:
            if not (c.trial and c.series is not None):
                continue
            r = rows[s][c.cid]
            r.setdefault("g1", False)
            good, det = [], {}
            for nb in sd.cells:
                if nb.trial or nb.D != c.D or nb.q != c.q:
                    continue
                src = nb if nb.series is not None else by.get(nb.dup_of)
                if src is None or src.series is None or src.cid not in rows[s]:
                    det[nb.cid] = None
                    continue
                rn = rows[s][src.cid]
                j = st.jaccard(r["mask"], nb.excl)
                det[nb.cid] = (round(rn["d"], 4), round(j, 3), rn["admitted"])
                if rn["admitted"] and rn["d"] == rn["d"] and rn["d"] >= st.PLATEAU * r["d"] and j <= st.JACCARD_MAX:
                    good.append(nb.cid)
            r["g5"] = bool(r["admitted"] and r["d"] > 0 and len(good) >= NEIGHBOUR_PLATEAU_MIN)
            r["g5_note"] = "соседи (Δ, Jaccard, допущен) %s; порог Δ %.4f; зачтены %s" % (det, st.PLATEAU * r["d"] if r["d"] == r["d"] else float("nan"), good)
            r["pass"] = bool(r["admitted"] and r["g1"] and r.get("g2") and r.get("g3") and r.get("g4") and r["g5"])
            if r["pass"]:
                passed.append((s, c.cid))
    return dict(rows=rows, holm=dict(m=len(trial), ps=ps, trial=trial), passed=passed)


def replicate(side, cell, om, nboot=st.NBOOT):
    """Повтор на o566 (п. 5): Δ > 0 в целом, знак по информативным месяцам (g2), (g3), (g4); допуск, Холм и плато не требуются. Возвращает строку клетки + `repl` (булево)."""
    r = cell_row(side, cell, np.asarray(om), nboot)
    r["repl"] = bool(r["d"] == r["d"] and r["d"] > 0 and r.get("g2") and r.get("g3") and r.get("g4"))
    return r


def side_outcome(ev, repl, side):
    """Исход стороны по п. 7: «работает» — клетка h775 прошла (g1)–(g5) и повторилась на o566; «не работает» — все допущенные пробные клетки h775 стороны (≥ 1) с Δ < 0; иначе «не ясно».
    ev — результат `evaluate`; repl — {(side, cid): строка replicate} (клетки, прошедшие на h775)."""
    works = [cid for s, cid in ev["passed"] if s == side and repl.get((s, cid), {}).get("repl")]
    adm = [r for r in ev["rows"][side].values() if r["trial"] and r["admitted"]]
    if works:
        return "работает", works
    if adm and all(r["d"] < 0 for r in adm):
        return "не работает", []
    return "не ясно", []


def answer_line(outcomes):
    """Одна строка ответа владельцу (п. 7): «работает» — хотя бы одна сторона; «не работает» — обе; иначе «не ясно». outcomes: {side: (исход, клетки)}."""
    names = {"L": "лонг", "S": "шорт"}
    w = [names[s] for s, (o, _) in outcomes.items() if o == "работает"]
    if w:
        return "работает (%s)" % ", ".join(w)
    if all(o == "не работает" for o, _ in outcomes.values()):
        return "не работает"
    return "не ясно"
