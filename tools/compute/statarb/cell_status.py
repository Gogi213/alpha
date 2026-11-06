#!/usr/bin/env python3
"""TK-493 w3 (M-06, M-11): статус клетки по P-37 §10.4 с поправками аудита.

Что изменено против текста P-37 §10.4 (всё - ПО ЗАПИСИ в docs/findings/xdl9-stats-fixes-2026-11-06.md, ждёт подтверждения Судьи):
  (а) нижняя граница 90 %-интервала оценки клетки > 0 - интервал `portfolio.block_t_interval` (t по блокам, df = n_эфф−1; M-04),
      а не перцентильный бутстреп;
  (б) вместо «плацебо в нуле» (M-06): нижняя граница 90 %-интервала разности «клетка − контроль» по суткам в паре > 0;
      контроль - случайные входы с тем же числом сделок и распределением удержаний (P-37 §11.3), его суточный ряд приходит от вызывающего;
  (в) «без лучшей монеты» - фильтр, как в протоколе; «без лучших 2 суток» - только справка (поле `mean_without_best_days`), в статус не входит;
  (г), (д) - как в протоколе; пороги (30 сделок, 5 пар, 2 суток, 12,6 bps, 2,8) - из P-37 §10.4/§10.3 без изменений.
Статусы: candidate («Кандидат на проверку»), indistinguishable («Не различимо с нулём»), unclear («Не ясно», потолок); описательная
метка `minus` (верхняя граница < 0) - отдельным полем.

Единственное решение сверх текста протокола: клетка без единой сделки (N = 0) получает `unclear`, а не `indistinguishable`
(в протоколе §10.4 для «не различимо» условия N нет: пустой ряд даёт интервал [0, 0], σ = 0, МРЭ = 0 ≤ 12,6 - вывод «мощность достаточна»
на пустом месте). Флаг `empty_unclear=False` возвращает буквальное чтение протокола.

Только stdlib (интервал - `portfolio.block_t_interval`).
"""
import importlib.util
import math
import os
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))


_PORTFOLIO = []


def _portfolio():
    """portfolio.py грузится под собственным именем: в sys.modules под именем `portfolio` могут лежать другие модули проекта."""
    if not _PORTFOLIO:
        spec = importlib.util.spec_from_file_location("statarb_portfolio_cell_status", os.path.join(HERE, "portfolio.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _PORTFOLIO.append(mod)
    return _PORTFOLIO[0]


# Пороги протокола P-37 (все - из текста протокола; значения не менялись)
ALPHA = 0.10            # двусторонний уровень интервала: 90 % (Н11)
MIN_TRADES = 30         # (г) не менее 30 закрытых сделок (Н9)
MIN_PAIRS = 5           # (г) и не менее 5 различных пар со сделкой (Н9)
BEST_DAYS = 2           # «лучшие 2 суток» - справка (Н15)
MRE_COEF = 2.8          # П-32 §8 (из источника); см. mre_coef_for() - расхождение с интервалом, M-11
MRE_THRESHOLD_BPS = 12.6  # круг двух ног (Н14); единицы разные с МРЭ, конвенция П-32 §8 (оговорка P-37 §10.4)

CANDIDATE, INDISTINGUISHABLE, UNCLEAR = "candidate", "indistinguishable", "unclear"
LABEL_RU = {CANDIDATE: "Кандидат на проверку", INDISTINGUISHABLE: "Не различимо с нулём", UNCLEAR: "Не ясно"}


def n_eff_blocks(n_days, block=None):
    """n_эфф = число блоков ⌈n/block⌉ (P-37 §10.2: ⌈n_дней/3⌉). Это определение, а не измерение: его df = n_эфф−1 стоит в t-интервале."""
    b = block or _portfolio().block_len(n_days)
    return math.ceil(n_days / b)


def mre_coef_for(alpha_two_sided=ALPHA, power=0.80):
    """Множитель МРЭ, согласованный с правилом «нижняя граница 90 %-интервала > 0» (односторонний уровень alpha/2) при мощности `power`:
    z_{1−alpha/2} + z_{power} (нормальные квантили). Для alpha = 0,10 и 80 % это 2,487; протокольные 2,8 = 1,96 + 0,84 - двусторонний 5 %."""
    nd = statistics.NormalDist()
    return nd.inv_cdf(1 - alpha_two_sided / 2) + nd.inv_cdf(power)


def mre(sigma, n_eff, coef=MRE_COEF):
    """МРЭ = coef·σ/√n_эфф (P-37 §10.3), в единицах σ. coef по умолчанию - протокольный 2,8."""
    if n_eff <= 0:
        raise ValueError("n_эфф ≤ 0")
    return coef * sigma / math.sqrt(n_eff)


def mean_without_best_days(series, k=BEST_DAYS):
    """Среднее суточных значений без k лучших суток (справка, не фильтр, M-06). При n ≤ k - None."""
    if len(series) <= k:
        return None
    rest = sorted(series)[:-k]
    return sum(rest) / len(rest)


def classify(cell_days, control_days, *, n_trades, n_pairs, mean_mtm, sigma_form, mean_without_best_coin,
             block=None, empty_unclear=True, fee_burden_bps=None):
    """Статус клетки; возвращает словарь.

    cell_days     - суточные значения клетки (bps/сутки/активную пару) на торговые сутки, подряд;
    control_days  - ряд контроля «случайные входы» на те же сутки (тот же порядок и длина) или None, если контроль недоступен
                    (тогда (б) не выполнено, кандидатом клетка стать не может);
    n_trades, n_pairs - закрытых сделок и различных пар со сделкой;
    mean_mtm      - оценка клетки с MTM открытых позиций (условие (д)); None - нет данных → (д) не выполнено;
    sigma_form    - σ суточной метрики по формированию (§6.6) или None;
    mean_without_best_coin - среднее суточных значений без пар лучшего базового актива (условие (в)) или None → (в) не выполнено;
    fee_burden_bps - измеренное суточное бремя комиссий клетки (fee_bps на активную пару-сутки, колонка вывода ядра), если есть:
                     только справка `mre_over_fee_burden` (M-11: порог 12,6 bps - издержки круга одной сделки, МРЭ - в bps/сутки/пару);
                     в статус не входит.
    """
    P = _portfolio()
    n = len(cell_days)
    if n == 0:
        raise ValueError("пустой ряд клетки")
    if control_days is not None and len(control_days) != n:
        raise ValueError("ряд контроля не той же длины")
    mean, lo, hi, p, df = P.block_t_interval(list(cell_days), ALPHA, block)
    cond_a = lo > 0
    if control_days is None:
        diff = None
        cond_b = False
    else:
        diff = P.block_t_interval([c - k for c, k in zip(cell_days, control_days)], ALPHA, block)
        cond_b = diff[1] > 0
    cond_c = mean_without_best_coin is not None and mean_without_best_coin > 0
    cond_d = n_trades >= MIN_TRADES and n_pairs >= MIN_PAIRS
    cond_e = mean_mtm is not None and mean_mtm > 0
    n_eff = n_eff_blocks(n, block)
    mre_val = None if sigma_form is None else mre(sigma_form, n_eff)
    contains_zero = lo <= 0.0 <= hi
    indist = contains_zero and mre_val is not None and mre_val <= MRE_THRESHOLD_BPS and not (empty_unclear and n_trades == 0)
    if cond_a and cond_b and cond_c and cond_d and cond_e:
        status = CANDIDATE
    elif indist:
        status = INDISTINGUISHABLE
    else:
        status = UNCLEAR
    return {
        "status": status, "label": LABEL_RU[status], "minus": hi < 0.0,
        "mean": mean, "lo": lo, "hi": hi, "p": p, "df": df, "n_days": n, "n_eff": n_eff,
        "cond_a": cond_a, "cond_b": cond_b, "cond_c": cond_c, "cond_d": cond_d, "cond_e": cond_e,
        "diff_mean": None if diff is None else diff[0], "diff_lo": None if diff is None else diff[1],
        "diff_hi": None if diff is None else diff[2],
        "mean_without_best_days": mean_without_best_days(cell_days),  # справка (M-06), в статус не входит
        "mre": mre_val,
        "mre_over_fee_burden": (None if mre_val is None or not fee_burden_bps else mre_val / fee_burden_bps),  # справка (M-11)
    }


def expected_false_candidates(m_cells, size=0.05):
    """Ожидаемое число ложных «кандидатов» при нуле: size·M. size - измеренный размер правила (size_test.py), номинал 0,05;
    клетки зависимы (общий рынок, общие активы) - число ориентир, не оценка (P-37 §10.4)."""
    return size * m_cells
