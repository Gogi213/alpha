"""П-42 §13.27 (TK-610): общая логика — C* из заморозки TK-599, ключи R0 / R1 / R2, сутки от 01.07, блоки Б1…Б4 с очисткой и зоной августа, оценка кандидата
(OOF / среднее четырёх моделей), слияние с фильтром F, вердикт августа, охрана запечатанности.

Чистые функции без обращения к диску (кроме `load_cstar`, `sha256_file`), поэтому тестируются на синтетике (`test_tk610.py`). Протокол — `docs/research/P-42-level-breakout.md` §13.27.
Статистика Δ, устойчивость, склейка окон — `retitr_full.py` (§13.24); подгонка, очистка, метки — `tk598_lib.py` (§13.23).
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import retitr_cells as rc  # noqa: E402
import retitr_full as rf  # noqa: E402
import tk598_lib as L598  # noqa: E402

GuardError = rf.GuardError
DAY = rf.DAY_MS
D0 = rf.D0
SEED = 610
FEATS = L598.FEATS
KEY = L598.KEY
GRID = L598.GRID
ITER_CAP = 1000
OD_WAIT = 50
MIN_ITERS = 10
MIN_ROWS = 3000                            # строк U0* на сторону в окне поиска (§13.27 п. 2)
MIN_TRADES_AUG = 30                        # порог применимости вердикта (п. 6)
# блоки CV — календарные, сутки от 01.07 включительно (п. 3): Б1 июль (31), Б2 сентябрь (30), Б3 октябрь (31), Б4 01–13.11 (13)
BLOCKS = [(0, 30), (62, 91), (92, 122), (123, 135)]
BLOCK_NAMES = ("Б1", "Б2", "Б3", "Б4")
AUG = (31, 61)                             # август: сутки 31…61 от 01.07 (31 сут.)
ZONE = (30 * DAY, 63 * DAY)                # зона августа [31.07 00:00; 02.09 00:00) UTC, мс от 01.07
SCORE_COL = "score610"
NEG = -1e30                                # оценка у кандидатов, не прошедших F
PASS_LO, PASS_HI = "-1e18", "1e18"         # клетки G5
Z_ONE_SIDED = 1.645
Z_POWER = 0.84


# ───────────────────────────── C* и ключи ─────────────────────────────

def load_cstar(src):
    """C* по сторонам из `tk599-frozen.json` (формат `tk599-frozen-1`, §13.24 п. 6): `sides[s]["keys"]` — ОДНА строка ключей движка без id варианта.
    Допустим словарь `{L|S: "ключи"}` (сухой прогон на базах). Возвращает {сторона: {keys, decision, filter}}; filter — {fa, fo, fv} или None; больше одного фильтра — GuardError."""
    if isinstance(src, (str, os.PathLike)):
        with open(src, encoding="utf-8") as f:
            src = json.load(f)
    sides = src.get("sides", src)
    out = {}
    for s in rf.SIDES:
        e = sides.get(s)
        if e is None:
            raise GuardError("в C* нет стороны %s" % s)
        keys = e.get("keys") if isinstance(e, dict) else e
        if not isinstance(keys, str) or not keys.strip():
            raise GuardError("C* стороны %s: ключи — не непустая строка: %r" % (s, keys))
        _, feats = rc.parse_keys(keys)
        if len(feats) > 1:
            raise GuardError("C* стороны %s: больше одного фильтра признака (fa/fo/fv): %s" % (s, keys))
        flt = feats[0] if feats else None
        if flt is not None and set(flt) != {"fa", "fo", "fv"}:
            raise GuardError("C* стороны %s: фильтр признака неполный: %s" % (s, flt))
        out[s] = {"keys": keys.strip(), "decision": e.get("decision") if isinstance(e, dict) else None, "filter": flt}
    return out


def strip_filter(keys):
    """Ключи без `fa/fo/fv`."""
    plain, _ = rc.parse_keys(keys)
    return " ".join("%s=%s" % kv for kv in plain)


R0_OFF = "natr_min=0 t1h=0 t24=0"          # пул выключен (п. 2)


def r0_keys(keys):
    """R0 = U0*: ключи C* без фильтра признака и без фильтров пула, режим SIGMODE (§13.21.1 п. 3)."""
    return rc.merge_keys(strip_filter(keys), R0_OFF, rc.SIGMODE)


def r1_keys(keys):
    return keys.strip()


def r2_keys(keys, q):
    """R2 = C* с фильтром по оценке: прежний `fa/fo/fv` заменён на `fa=score610 fo=lo fv=<q>` (движок: `lo` отбрасывает `value < fv`)."""
    return rc.merge_keys(strip_filter(keys), "fa=%s fo=lo fv=%s" % (SCORE_COL, repr(float(q))))


def g5_keys(keys, kind):
    """Клетки G5: `pass` — fv = −1e18 (не режет ничего сверх F), `cut` — fv = 1e18 (режет всех)."""
    fv = {"pass": PASS_LO, "cut": PASS_HI}[kind]
    return rc.merge_keys(strip_filter(keys), "fa=%s fo=lo fv=%s" % (SCORE_COL, fv))


def variants_text(rows):
    """`<id> <ключи>\\n` по строкам [(id, ключи)]."""
    return "".join("%s %s\n" % (i, k) for i, k in rows)


def variant_rows(cstar, q=None):
    """Варианты прогонов стороны: {R: [(id, ключи)]}. R0 — `<S>_r0`; R1 — `<S>_r1`; R2 (при заданном `q`) — `<S>_r2` и клетки G5 `<S>_g5pass`, `<S>_g5cut`.
    Имя начинается с буквы стороны (`tk598_feat.build` берёт сторону по ней)."""
    out = {"R0": [], "R1": [], "R2": []}
    for s, c in cstar.items():
        out["R0"].append(("%s_r0" % s, r0_keys(c["keys"])))
        out["R1"].append(("%s_r1" % s, r1_keys(c["keys"])))
        if q is not None and s in q:
            out["R2"].append(("%s_r2" % s, r2_keys(c["keys"], q[s])))
            out["R2"].append(("%s_g5pass" % s, g5_keys(c["keys"], "pass")))
            out["R2"].append(("%s_g5cut" % s, g5_keys(c["keys"], "cut")))
    return out


# ───────────────────────────── сутки, блоки, зона августа ─────────────────────────────

def win_offset_ms(win):
    """Смещение T0 окна от 2026-07-01, мс."""
    return (win.t0d - D0).days * DAY


def credit_days(win):
    """Зачётный диапазон окна — сутки от 01.07 включительно."""
    return (win.lod - D0).days, (win.hid - D0).days


def block_of_abs(day, blocks=BLOCKS):
    """Номер блока CV по суткам от 01.07; −1 — вне блоков (август)."""
    day = np.asarray(day)
    b = np.full(len(day), -1, np.int64)
    for i, (d0, d1) in enumerate(blocks):
        b[(day >= d0) & (day <= d1)] = i
    return b


def is_aug(day):
    day = np.asarray(day)
    return (day >= AUG[0]) & (day <= AUG[1])


def zone_hit(a_entry, a_exit):
    """Интервал сделки `[entry, exit]` пересекает зону августа."""
    a_entry, a_exit = np.asarray(a_entry), np.asarray(a_exit)
    return (a_exit >= ZONE[0]) & (a_entry < ZONE[1])


def assert_search_days(day, what="строки"):
    """G6: обучение и подгонка читают только сутки окна поиска (блоки Б1…Б4); август, зона и любые иные — остановка."""
    d = np.asarray(day)
    if len(d) and (block_of_abs(d) < 0).any():
        bad = np.unique(d[block_of_abs(d) < 0])[:5].tolist()
        raise GuardError("G6: %s содержат сутки вне окна поиска (первые: %s)" % (what, bad))


def fold_train_idx(aday, a_entry, a_exit, k, blocks=BLOCKS):
    """Обучающие строки фолда `k`: очистка по блоку (по 1 суткам с обеих сторон, пересечение интервала с расширенным блоком — `tk598_lib.purge_train`)
    без строк, чей интервал пересекает зону августа."""
    idx = L598.purge_train(aday, a_entry, a_exit, blocks[k])
    return idx[~zone_hit(np.asarray(a_entry)[idx], np.asarray(a_exit)[idx])]


# ───────────────────────────── оценка кандидата, порог, фильтр ─────────────────────────────

def pick_scores(block, preds, blocks=BLOCKS, strict=True):
    """`ŝ` кандидата: блок поиска `k` → предсказание фолда `k` (OOF); иначе (август) — среднее фолдовых моделей. `preds` — {k: предсказания всех кандидатов модели k}.
    `strict`: должны быть все модели блоков (иначе GuardError); без него (сухой прогон) блок без модели оценивается средним имеющихся."""
    block = np.asarray(block)
    if not preds:
        raise GuardError("нет фолдовых моделей")
    if strict and set(preds) != set(range(len(blocks))):
        raise GuardError("не все фолдовые модели: %s" % sorted(preds))
    mean_all = np.mean([np.asarray(p, float) for p in preds.values()], axis=0)
    out = mean_all.copy()
    for k, p in preds.items():
        m = block == k
        out[m] = np.asarray(p, float)[m]
    return out


def threshold_q(s_oof):
    """Порог «верхней трети»: квантиль 2/3 внефолдовых `ŝ` (NaN не считаются)."""
    s = np.asarray(s_oof, float)
    s = s[~np.isnan(s)]
    if len(s) == 0:
        raise GuardError("нет оценок для порога")
    return float(np.quantile(s, 2 / 3))


def f_pass(x, fo, fv):
    """Решение фильтра F как у движка (`ftab.rs`): NaN проходит; `lo` — `x ≥ fv`, `hi` — `x ≤ fv`."""
    x = np.asarray(x, float)
    with np.errstate(invalid="ignore"):
        bad = (x < fv) if fo == "lo" else (x > fv)
    return ~bad


def merge_score(shat, passed):
    """Один столбец фильтра: `ŝ` у прошедших F, `−1e30` у не прошедших. NaN-оценка — остановка (движок молча пропустил бы)."""
    shat = np.asarray(shat, float)
    if np.isnan(shat).any():
        raise GuardError("ŝ = NaN у %d кандидатов: NaN проходит фильтр движка" % int(np.isnan(shat).sum()))
    return np.where(np.asarray(passed, bool), shat, NEG)


def selected_share(s, q):
    s = np.asarray(s, float)
    return float((s >= q).mean()) if len(s) else float("nan")


# ───────────────────────────── вердикт августа ─────────────────────────────

def aug_slice(ser):
    """Суточные суммы и счётчики ряда за август (сутки 31…61): массивы длины 31."""
    S, N = rf.day_sums(ser)
    return S[AUG[0]:AUG[1] + 1], N[AUG[0]:AUG[1] + 1]


def delta_upper(Sc, Nc, Sr, Nr, nboot=rf.NBOOT):
    """Верхняя односторонняя 95 %-граница Δ — тот же бутстреп, что у `rf.delta_stat` (то же зерно и блоки), 95-й перцентиль."""
    Sc, Nc, Sr, Nr = (np.asarray(a, float) for a in (Sc, Nc, Sr, Nr))
    idx = rf.block_idx(len(Sc), np.random.default_rng(rf.seed_of(Sc, Nc, Sr, Nr)), nboot)
    bSc, bNc, bSr, bNr = (a[idx].sum(axis=1) for a in (Sc, Nc, Sr, Nr))
    ok = (bNc > 0) & (bNr > 0)
    if ok.mean() < 0.99:
        raise GuardError("бутстреп: у %.1f %% повторов нет сделок у B или A" % (100 * (1 - ok.mean())))
    return float(np.percentile(bSc[ok] / bNc[ok] - bSr[ok] / bNr[ok], 95))


def aug_verdict(st, upper, robust_ok, min_trades=MIN_TRADES_AUG):
    """Вердикт стороны (§13.27 п. 6). `st` — `rf.DStat` с c = B (с фильтром), r = A (C* без фильтра): `n_c` = сделок B, `n_r` = сделок A, `s_c` = ΣS_B.
    «не определено» — сделок B или A < 30; «подтверждено» — `lower(Δ) > 0`, `ΣS_B > 0`, `ΔΣ ≥ −1·SE_Σ`, `robust`; «опровергнуто» — верхняя граница Δ < 0; иначе «не ясно»."""
    if st.n_c < min_trades or st.n_r < min_trades:
        return "не определено"
    if st.lower > 0 and st.s_c > 0 and st.dsum >= -1.0 * st.se_sum and robust_ok:
        return "подтверждено"
    if upper < 0:
        return "опровергнуто"
    return "не ясно"


def mde_pair(sigma, n_b, n_a, upper=False):
    """Различимый эффект Δ (п. 7): `(1,645 + 0,84)·σ·√(1/n_B ∓ 1/n_A)`; нижняя оценка — знак «−» (B ⊂ A), верхняя — «+» (портфель: B не подмножество A)."""
    if n_b <= 0 or n_a <= 0:
        return float("nan")
    v = 1.0 / n_b + (1.0 if upper else -1.0) / n_a
    return float((Z_ONE_SIDED + Z_POWER) * sigma * math.sqrt(max(v, 0.0)))


def describe_blocks(aday, net, shat, q, blocks=BLOCKS):
    """Описательно (п. 6): по блокам — S0 = среднее net выбранных (`ŝ ≥ q`), S1 = S0 − среднее всех, числа; «найдено на этих данных», решения не несёт."""
    aday, net, shat = np.asarray(aday), np.asarray(net, float), np.asarray(shat, float)
    blk = block_of_abs(aday, blocks)
    out = []
    for k in range(len(blocks)):
        m = blk == k
        sel = m & (shat >= q)
        n_all, n_sel = int(m.sum()), int(sel.sum())
        mean_all = float(net[m].mean()) if n_all else float("nan")
        s0 = float(net[sel].mean()) if n_sel else float("nan")
        out.append({"block": BLOCK_NAMES[k], "n_all": n_all, "n_sel": n_sel, "mean_all": mean_all, "S0": s0, "S1": s0 - mean_all if n_sel and n_all else float("nan")})
    return out


def age_censor(age_h, days=14):
    """G9: число и доля строк с `age_h` > `days` суток (часы)."""
    a = np.asarray(age_h, float)
    n = int((a > days * 24).sum())
    return {"rows": int(len(a)), "gt_days": n, "share": float(n / len(a)) if len(a) else float("nan")}


# ───────────────────────────── ряды августа ─────────────────────────────

def aug_windows():
    """Окна, чьи зачётные диапазоны дают август: wa 01–25.08, wb 26–31.08 (как `splice` TK-599)."""
    wa, wb = rf.WINDOWS[0], rf.WINDOWS[1]
    return (dataclasses.replace(wa, lo="2026-08-01", hi="2026-08-25"), dataclasses.replace(wb, lo="2026-08-26", hi="2026-08-31"))


def splice_aug(tables, coins, daily=None):
    """Склейка августа (31 сутки) из `wa` и `wb` с охраной G6 §13.24: net = gross − fees + funding по строкам, монеты в таблице, Σ daily среза = Σ net среза, Σ склейки = Σ среза.
    tables / daily — {окно: …}; Series в сутках от 01.07 (длина осей — NDAYS; заняты сутки 31…61)."""
    ci = {c: i for i, c in enumerate(coins)}
    parts, tot = [], 0.0
    for w in aug_windows():
        t = tables[w.name]
        d = rf.abs_day(w, t.entry_ms)
        if len(t) and np.abs(t.net - (t.gross - t.fees + t.funding)).max() > 2e-6:
            raise GuardError("%s: net ≠ gross − fees + funding в строке сделки" % w.name)
        lo_i, hi_i = (w.lod - D0).days, (w.hid - D0).days
        sel = np.nonzero((d >= lo_i) & (d <= hi_i))[0]
        unknown = sorted({t.sym[i] for i in sel if t.sym[i] not in ci})
        if unknown:
            raise GuardError("%s: монеты вне таблицы: %s" % (w.name, ", ".join(unknown[:5])))
        val = (t.gross - t.fees)[sel]
        tot += float(val.sum())
        if daily is not None and daily.get(w.name) is not None:
            first = (w.lod - w.t0d).days - rf.GRID_START_DAY
            last = (w.hid - w.t0d).days - rf.GRID_START_DAY
            ssum = sum(daily[w.name].get(i, 0.0) for i in range(first, last + 1))
            nsum = float(t.net[sel].sum())
            tol = max(1e-4, 2e-6 * len(sel))
            if abs(ssum - nsum) > tol:
                raise GuardError("%s: Σ daily среза %+.6f ≠ Σ net сделок среза %+.6f" % (w.name, ssum, nsum))
        off = rf._t0_ms(w)
        parts.append((d[sel], np.array([ci[t.sym[i]] for i in sel], dtype=np.int64), val, t.funding[sel], t.entry_ms[sel] + off, t.exit_ms[sel] + off))
    cat = lambda j: np.concatenate([p[j] for p in parts])  # noqa: E731
    ser = rf.make_series(cat(0), cat(1), cat(2), cat(3), None, cat(4), cat(5))
    if abs(float(ser.val.sum()) - tot) > 1e-6 * max(1, len(ser.val)):
        raise GuardError("Σ склейки августа ≠ Σ зачётных строк")
    if len(ser.day) and (ser.day.min() < AUG[0] or ser.day.max() > AUG[1]):
        raise GuardError("после склейки сутки вне августа")
    return ser


def aug_compare(ser_a, ser_b, ncoins, nboot=rf.NBOOT):
    """Сторона: Δ = B − A за август. Возвращает {st, upper, robust, verdict, ...}; A — C* без фильтра (R1), B — с фильтром (R2)."""
    Sa, Na = aug_slice(ser_a)
    Sb, Nb = aug_slice(ser_b)
    st = rf.delta_stat(Sb, Nb, Sa, Na, nboot)
    up = delta_upper(Sb, Nb, Sa, Na, nboot)
    om = np.arange(AUG[0], AUG[1] + 1)
    rob, dd, dc = rf.robust(ser_b, ser_a, om, ncoins)
    return {"stat": st, "upper": up, "robust_ok": rob, "robust_days": dd, "robust_coins": dc, "verdict": aug_verdict(st, up, rob),
            "sum_a": float(Sa.sum()), "sum_b": float(Sb.sum()), "n_a": int(Na.sum()), "n_b": int(Nb.sum()),
            "mean_a": rf.ratio(Sa.sum(), Na.sum()), "mean_b": rf.ratio(Sb.sum(), Nb.sum()),
            "days_pos_a": float((Sa > 0).mean()), "days_pos_b": float((Sb > 0).mean())}


# ───────────────────────────── файлы ─────────────────────────────

def sha256_file(path):
    return rf.sha256_file(path)


def md5_file(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def code_sha256(here=HERE):
    """sha256 кода заморозки: `tk610_*.py`, `tk598_*.py`, `retitr_full.py`, `retitr_ftab.py`, `retitr_cells.py`."""
    names = sorted(fn for fn in os.listdir(here) if (fn.startswith(("tk610_", "tk598_")) and fn.endswith(".py")) or fn in ("retitr_full.py", "retitr_ftab.py", "retitr_cells.py"))
    return {fn: sha256_file(os.path.join(here, fn)) for fn in names}


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()
