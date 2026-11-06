#!/usr/bin/env python3
"""TK-493 S1 (П-37 r3.1 §7.3, §7.3а), S2 (r3.2 §7.2, D13): оценка доли ложных отборов A1 по эмпирическому нулю в единицах РАЗЛИЧНЫХ БАЗОВЫХ АКТИВОВ.
Общий модуль для sel_fdr.py и тестов (аудит M-10: функции оценки вынесены из main()). Только numpy.

ТОЧНОЕ ПРАВИЛО (операциональное чтение §7.3а-(ii)-(iv), зафиксировано в коде ДО пересчёта; НАЗНАЧЕНО CEO-сессией без Судьи, подтвердить):
  1. Единица счёта - база. В каталоге у базы один основной контракт (§4.1), поэтому база = символ каталога.
  2. Жадное паросочетание. Пары набора (набор = реальные пары каталога ИЛИ нулевые пары ОДНОГО разбиения при ОДНОМ сдвиге) упорядочиваются
     по возрастанию статистики (при равенстве - по паре индексов символов, символы пронумерованы по отсортированным именам); пара
     принимается, если ни одна из её баз ещё не принята. Звезда из сотен пар вокруг одного символа даёт одну принятую пару.
     Свойство префикса: принятые пары со статистикой <= t - это жадный результат на подграфе пар со статистикой <= t, поэтому
     паросочетание строится один раз для всех пар набора, а k0(t) и R(t) читаются как число принятых пар со статистикой <= t.
  3. Нуль (r3.2, D13: НА ТЕХ ЖЕ РЁБРАХ, что реальные пары): для каждого из 9 повторов паросочетание строится отдельно на ВСЕХ рёбрах реального
     графа (пара «внутри половины» хотя бы в одном из разбиений 1-3), у каждого ребра статистика повтора - статистика ЭТОЙ ЖЕ пары активов при
     другом выравнивании рядов (sel_shift: 12 выравниваний на ребро, свой случайный порядок на ребро, повторы 0-8 - обучение, 9-11 - контроль G9);
     k0(t) - сумма принятых пар <= t по повторам; n0 - сумма размеров полных паросочетаний повторов. Реальные: M - размер полного паросочетания
     реальных пар каталога, R(t) - принятые реальные пары <= t. (r3.1: нуль был двудольным графом A x B с ≈ 0,57 рёбер реального графа - оценка
     FDR оптимистична ≈ в 1,75 раза; 21-25 % нулевых статистик были точными копиями, аудит M2-01, M2-02.)
  4. FDR(t) = (k0(t)+1) / (n0+1) * M / max(1, R(t)); считается в точках t = статистика принятой реальной пары.
     q(t) = min_{t' >= t} FDR(t') (кумулятивный минимум от нестрогого края); t* - наименее строгий t с q(t) <= q (q = 0,10), что равно
     наибольшей точке с FDR <= q. Допуск: k0(t*) >= 5 в единицах баз, иначе «отбор не выполнен».
  5. Реальный отбор устроен так же, как нулевой («на реальном и нулевом выборе одинаково»): отбираются принятые паросочетанием реальные
     пары со статистикой <= t* (каждая база не более одного раза), и торгуются ВСЕ они (§7.4, без топ-N). Реальные пары <= t*, не
     попавшие в паросочетание (их база уже занята более сильной парой), не торгуются и считаются в отчёте. Рядом печатаются те же
     числа в единицах пар. ОГРАНИЧЕНИЕ (синтетика, тест test_statarb_sel_fdr): оценка оптимистична - при заявленных 0,10 доля ложных
     среди отобранных в синтетике с сигналом 0,15-0,25 (выбор t* по максимуму кривой, малые k0); это порядок величины, не равенство.
"""
import numpy as np

import sel_shift as SH

Q_LEVEL = 0.10
K0_MIN = 5


def greedy_matching(lo, hi, t, nsym=None):
    """Жадное паросочетание по возрастанию статистики. lo, hi - целые индексы баз (lo != hi), t - статистики.
    -> индексы принятых пар во входных массивах в порядке принятия (статистика по возрастанию)."""
    lo = np.asarray(lo, dtype=np.int64)
    hi = np.asarray(hi, dtype=np.int64)
    t = np.asarray(t, dtype=np.float64)
    n = len(t)
    if n == 0:
        return np.zeros(0, dtype=np.int64)
    a = np.minimum(lo, hi)
    b = np.maximum(lo, hi)
    order = np.lexsort((b, a, t))           # t, затем (a, b)
    nbase = int(nsym) if nsym is not None else int(max(a.max(), b.max())) + 1
    used = bytearray(nbase)
    al, bl = a[order].tolist(), b[order].tolist()
    acc = []
    left = nbase
    for r in range(n):
        x, y = al[r], bl[r]
        if not used[x] and not used[y]:
            used[x] = 1
            used[y] = 1
            acc.append(r)
            left -= 2
            if left < 2:
                break
    return order[np.asarray(acc, dtype=np.int64)]


def accepted_stats(lo, hi, t, nsym=None):
    """Статистики принятых пар, по возрастанию."""
    idx = greedy_matching(lo, hi, t, nsym)
    return np.asarray(t, dtype=np.float64)[idx]


def fdr_curve(real_acc, null_acc):
    """real_acc, null_acc - отсортированные по возрастанию статистики принятых пар (реальные; все нулевые наборы вместе).
    M = len(real_acc), n0 = len(null_acc). -> (k0, R, fdr, q) в точках real_acc."""
    real_acc = np.asarray(real_acc, dtype=np.float64)
    null_acc = np.asarray(null_acc, dtype=np.float64)
    M, n0 = len(real_acc), len(null_acc)
    R = np.searchsorted(real_acc, real_acc, side="right")
    k0 = np.searchsorted(null_acc, real_acc, side="right")
    fdr = (k0 + 1.0) / (n0 + 1.0) * M / np.maximum(1, R)
    q = np.minimum.accumulate(fdr[::-1])[::-1]
    return k0, R, fdr, q


def select_threshold(real_acc, null_acc, q_level=Q_LEVEL, k0_min=K0_MIN):
    """-> dict(status, kind, ok, t_star, k0, R, fdr, fdr_hi, q, M, n0, fdr_min, R_at_min, k0_at_min).
    kind: 'ok' (отобрано) | 'strong' (отбор не выполнен: k0(t*) < k0_min, при этом сильные пары есть: R_base, k0_base печатаются) |
    'none' (пар нет: FDR(t) > q при любом t) | 'few' (мало пар: M < 10 или n0 < 9 в единицах баз).
    fdr_hi = min(1, 3 * fdr): условная поправка на оптимистичность оценки (r3.2, D13, L5-03: x1...3) - оценка FDR печатается диапазоном."""
    real_acc = np.sort(np.asarray(real_acc, dtype=np.float64))
    null_acc = np.sort(np.asarray(null_acc, dtype=np.float64))
    M, n0 = len(real_acc), len(null_acc)
    out = dict(status="", kind="few", ok=False, t_star=float("nan"), k0=0, R=0, fdr=float("nan"), fdr_hi=float("nan"), q=float("nan"), M=M, n0=n0,
               fdr_min=float("nan"), R_at_min=0, k0_at_min=0)
    if M < 10 or n0 < 9:
        out["status"] = "нет отбора: мало пар (M=%d, n0=%d в единицах баз)" % (M, n0)
        return out
    k0, R, fdr, q = fdr_curve(real_acc, null_acc)
    im = int(np.argmin(fdr))
    out.update(fdr_min=float(fdr[im]), R_at_min=int(R[im]), k0_at_min=int(k0[im]))
    good = np.where(fdr <= q_level)[0]
    if len(good) == 0:
        out["kind"] = "none"
        out["status"] = "нет отбора: пар нет (FDR(t) > %.2f при любом t; наименьшая оценка %.3f при R_base = %d, k0_base = %d)" % (
            q_level, out["fdr_min"], out["R_at_min"], out["k0_at_min"])
        return out
    i = int(good[-1])
    out.update(t_star=float(real_acc[i]), k0=int(k0[i]), R=int(R[i]), fdr=float(fdr[i]), fdr_hi=float(min(1.0, 3.0 * fdr[i])), q=float(q[i]))
    if out["k0"] < k0_min:
        out["kind"] = "strong"
        out["status"] = "отбор не выполнен: сильные пары есть (R_base = %d, k0_base = %d < %d в единицах баз)" % (out["R"], out["k0"], k0_min)
        return out
    out["kind"] = "ok"
    out["status"] = "отобрано"
    out["ok"] = True
    return out


# ---------------------------------------------------------------- нуль на рёбрах реального графа (r3.2, D13)
def draw_alignments(n_edges, seed=SH.ALIGN_SEED):
    """Для каждого ребра - случайная перестановка 12 выравниваний (RandomState(seed), ребра в заданном порядке): столбцы 0..N_TRAIN-1 - повторы
    для порога t*, остальные - контрольные повторы G9. -> int16 [n_edges, N_ALIGN]; выравнивание k: запуск k // 2, k % 2 == 0 - серия ребра с МЕНЬШИМ индексом
    взята с первым смещением запуска."""
    rs = np.random.RandomState(seed)
    return np.argsort(rs.random_sample((n_edges, SH.N_ALIGN)), axis=1).astype(np.int16)


def repeat_stats(TT, lo, hi, perm, j):
    """Нулевая статистика повтора j для каждого ребра (lo, hi) - ЭТОЙ ЖЕ пары активов. TT [запусков, ns, ns]: TT[run, a, b] - статистика пары, где серия
    символа a взята с первым смещением запуска, символа b - со вторым (NaN, если пары нет)."""
    k = perm[:, j].astype(np.int64)
    run = k // 2
    first_lo = (k % 2) == 0
    a = np.where(first_lo, lo, hi)
    b = np.where(first_lo, hi, lo)
    return TT[run, a, b]


def repeat_matchings(TT, lo, hi, perm, js, ns):
    """Паросочетания повторов js на рёбрах (lo, hi): -> список (lo, hi, t, принятые индексы) по повторам; рёбра с NaN-статистикой повтора выпадают из повтора."""
    out = []
    for j in js:
        t = repeat_stats(TT, lo, hi, perm, j)
        f = np.isfinite(t)
        idx = greedy_matching(lo[f], hi[f], t[f], ns)
        out.append((lo[f], hi[f], t[f], idx))
    return out


def pair_units(real_t, null_t_list, t_star):
    """Те же числа в единицах пар (справка): R, M, k0, n0, число различных символов среди нулевых пар <= t*: передаются
    (t, lo, hi); здесь только счёт по статистикам."""
    r = np.asarray(real_t, dtype=np.float64)
    nl = np.concatenate([np.asarray(x, dtype=np.float64) for x in null_t_list]) if null_t_list else np.zeros(0)
    return dict(M_pairs=len(r), R_pairs=int((r <= t_star).sum()), n0_pairs=len(nl), k0_pairs=int((nl <= t_star).sum()))


def distinct_symbols(lo, hi, t, t_star):
    """Число различных символов среди пар со статистикой <= t* (диагностика П-37 §7.3)."""
    t = np.asarray(t, dtype=np.float64)
    m = t <= t_star
    if not m.any():
        return 0
    return int(len(set(np.asarray(lo)[m].tolist()) | set(np.asarray(hi)[m].tolist())))


def matched_selection(lo, hi, t, t_star, nsym=None):
    """Реальный отбор (п. 5): индексы принятых паросочетанием пар со статистикой <= t*, по возрастанию статистики."""
    idx = greedy_matching(lo, hi, t, nsym)
    tt = np.asarray(t, dtype=np.float64)[idx]
    return idx[tt <= t_star]
