#!/usr/bin/env python3
"""TK-493 w3 (M-06, M-11) + w5 (D10-D12, аудиты L5-01, L5-02, L5-06, L6-01, L6-02, L6-03): статус клетки по P-37 §10.4 с поправками.

Что изменено против текста P-37 §10.4 (всё - ПО ЗАПИСИ, ждёт подтверждения Судьи; w3 - docs/findings/xdl9-stats-fixes-2026-11-06.md,
w5 - docs/findings/xdl9-stats-w5-2026-11-06.md; «назначено CEO без Судьи, подтвердить»):
  (а) нижние границы ДВУХ 90 %-интервалов основного ряда клетки > 0 (D12): блочный t `portfolio.block_t_interval` (w3, M-04) И студентизованный
      блочный бутстреп `portfolio.block_boot_t_interval` (B = 3000, seed 37 - уже назначенные числа Н10). Основной ряд - «закрытые сделки + MTM
      открытых на конце окна» (D10, L6-03): MTM относится к ПОСЛЕДНИМ суткам окна; закрытые сделки печатаются рядом (`closed_*`);
  (б) нижняя граница разности «клетка − контроль» по суткам в паре > 0 (w3, M-06); обе границы (t и бутстреп-t) - как в (а) (флаг `boot_on_diff`);
      контроль - случайные входы (P-37 §11.3), его ряд приходит от вызывающего и MTM не содержит (случайный вход не бывает «открыт на конце»);
  (в) «без лучшей монеты» - фильтр на закрытом варианте (поштучного MTM по заданиям в выходах ядра нет: `--cells-out` даёт сумму по клетке);
      «без лучших 2 суток» - только справка;
  (г) N >= 30 закрытых сделок и >= 5 пар - как в протоколе;
  (д) точечная оценка основного ряда (с MTM) > 0 - после D10 следует из (а), оставлена как проверка;
  (ж) НОВОЕ (D11, L6-01/L6-02): точечная оценка net при задержке +1 мин на ОБЕИХ ногах > 0 (вход - число от вызывающего; нет числа - (ж) не выполнено).
Статусы: candidate («Кандидат на проверку»), indistinguishable («Не различимо с нулём»), unclear («Не ясно», потолок); метка `minus` (верхняя
граница блочного t < 0) - описательная.

Решения сверх текста протокола: N = 0 → `unclear` (w3); недопустимые входы (σ_форм не конечна или <= 0; ряд с NaN/inf; интервал схлопнулся из-за
нулевой дисперсии блоков; один блок) не дают ни «кандидата», ни «не различимо» (w5, L5-06). Флаг `empty_unclear=False` возвращает буквальное чтение
протокола для N = 0; `indist_min_trades` (по умолчанию 0 = протокол) - минимум закрытых сделок для «не различимо» (L5-06 рекомендует 30, решает CEO/Судья).

Только stdlib (интервалы - `portfolio`; `m_eff` лениво берёт numpy).
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


# Пороги протокола P-37 (все - из текста протокола или params; значения не менялись, новых чисел нет)
ALPHA = 0.10            # двусторонний уровень интервала: 90 % (Н11)
MIN_TRADES = 30         # (г) не менее 30 закрытых сделок (Н9)
MIN_PAIRS = 5           # (г) и не менее 5 различных пар со сделкой (Н9)
BEST_DAYS = 2           # «лучшие 2 суток» - справка (Н15)
MRE_COEF = 2.8          # П-32 §8 (из источника); см. mre_coef_for() - расхождение с интервалом, M-11
MRE_THRESHOLD_BPS = 12.6  # круг двух ног (Н14); единицы разные с МРЭ, конвенция П-32 §8 (оговорка P-37 §10.4)
BOOT_B = 3000           # bootstrap_B (Н10): число ресэмплов студентизованного бутстрепа
BOOT_SEED = 37          # bootstrap_seed (Н10)
SHORT_TRADE_MIN = 2     # D11: «доля сделок длиной <= 2 мин» (колонка отчёта)

# D11 (аудит L6-01, L6-02): что такое open[t+1] у биржи. chain: open следующей минуты ВСЕГДА равен close предыдущей (P(open[t+1] = close[t]) = 1,0000
# на формировании), т.е. исполнение «по open t+1» = цена последней сделки сигнальной минуты; first_trade: open - первая сделка новой минуты.
OPEN_SEMANTICS = {"bybit": "chain", "bingx": "chain", "bitget": "chain", "kraken": "chain", "lighter": "chain",
                  "binance": "first_trade", "okx": "first_trade", "aster": "first_trade"}
OPEN_SEMANTICS_RU = {"chain": "цепочка (open[t+1] = close[t])", "first_trade": "первая сделка минуты", "mixed": "смешанная", "unknown": "не задана"}
DELAY0_NOTE = ("delay-0: исполнение по open[t+1]; на биржах «цепочка» (Bybit, BingX, Bitget, Kraken, Lighter) это цена последней сделки "
               "сигнальной минуты, а не граница исполнимого; читать только вместе со строкой «+1 мин на обеих ногах»")

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


# ------------------------------------------------------------------ D10: основной ряд = закрытые + MTM на последних сутках
def _finite(x):
    try:
        return x is not None and math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def build_series(day_net, day_active, days, *, mtm_net_sum=None, n_open=0, control_net=None):
    """Суточные ряды клетки по P-37 §10.1 с MTM открытых позиций (D10, L6-03). Вход - словари по суткам, не списки.

    day_net[d]    - Σ net_bps закрытых в сутки d сделок клетки (по всем заданиям);
    day_active[d] - число активных пар-суток (знаменатель §10.1); сутки без активных пар значения не дают (пропуск, не ноль);
    days          - сутки окна по порядку; последние сутки `days[-1]` получают MTM;
    mtm_net_sum   - Σ `mtm_net_bps` из `--cells-out` по клетке (позиции, открытые на конце окна, по последнему валидному close каждой ноги, минус
                    комиссия закрытия); None или NaN - MTM недоступен (ядро запущено без `--mtm-open`), тогда `mtm_available = False`, основной ряд = None;
    n_open        - `open_at_end` клетки (знаменатель MTM, если в последние сутки активных пар нет, а позиции есть);
    control_net   - Σ net случайных входов по суткам (f8_randentry `*.jobday.csv`) или None.

    Возврат: dict(days, closed, control_closed, primary, primary_days, control, mtm_available, mtm_value, mtm_denominator, mtm_day, mtm_day_added).
    `primary` = closed + MTM/знаменатель на последних сутках (если ряд там уже есть - прибавка к значению; если нет активных пар - добавляются
    сутки со значением MTM/n_open, `mtm_day_added = True`, сделки суток без активных пар в ряд по-прежнему не входят).
    Ряд контроля выровнен по `primary_days`: Σ net контроля / тот же знаменатель (на добавленных сутках - 0).
    """
    days = list(days)
    if not days:
        raise ValueError("пустое окно")
    last = days[-1]
    used = [d for d in days if day_active.get(d, 0) > 0]
    closed = [day_net.get(d, 0.0) / day_active[d] for d in used]
    out = dict(days=used, closed=closed, primary=None, primary_days=None, control=None, control_closed=None, mtm_available=False, mtm_value=0.0,
               mtm_denominator=None, mtm_day=last, mtm_day_added=False)
    if control_net is not None:
        out["control_closed"] = [control_net.get(d, 0.0) / day_active[d] for d in used]
    if not _finite(mtm_net_sum):
        return out
    out["mtm_available"] = True
    pdays, primary = list(used), list(closed)
    den_of = {d: day_active[d] for d in used}
    if mtm_net_sum != 0.0 or n_open > 0:
        if day_active.get(last, 0) > 0:
            den = day_active[last]
        elif n_open > 0:
            den = n_open
        else:
            raise ValueError("MTM не нулевой, а активных пар в последние сутки и открытых позиций нет")
        out["mtm_denominator"] = den
        out["mtm_value"] = mtm_net_sum / den
        if last in den_of:
            primary[pdays.index(last)] += out["mtm_value"]
        else:
            pdays.append(last)
            primary.append(out["mtm_value"])
            den_of[last] = den
            out["mtm_day_added"] = True
    out["primary"], out["primary_days"] = primary, pdays
    if control_net is not None:
        out["control"] = [control_net.get(d, 0.0) / den_of[d] for d in pdays]
    return out


def open_semantics(exchanges):
    """Семантика open клетки по биржам её ног: 'chain' / 'first_trade' / 'mixed' / 'unknown' (D11, L6-02)."""
    kinds = {OPEN_SEMANTICS.get(str(e).lower(), "unknown") for e in exchanges}
    if not kinds:
        return "unknown"
    if len(kinds) == 1:
        return next(iter(kinds))
    return "unknown" if "unknown" in kinds else "mixed"


def short_trade_share(durations_min, max_len=SHORT_TRADE_MIN):
    """Доля сделок длиной <= max_len минут (длина = fill_out − fill_in); None, если сделок нет (D11, L6-01)."""
    d = list(durations_min)
    if not d:
        return None
    return sum(1 for x in d if x <= max_len) / len(d)


# ------------------------------------------------------------------ статус
def classify(cell_days, control_days, *, n_trades, n_pairs, sigma_form, mean_without_best_coin,
             mtm_ok=True, closed_days=None, net_plus1=None, short_trade_share=None, exchanges=None, worst_trade=None,
             block=None, empty_unclear=True, fee_burden_bps=None, n_boot=BOOT_B, seed=BOOT_SEED, boot_on_diff=True, indist_min_trades=0):
    """Статус клетки; возвращает словарь.

    cell_days     - ОСНОВНОЙ суточный ряд клетки (bps/сутки/активную пару): закрытые сделки + MTM на последних сутках (`build_series()["primary"]`);
    control_days  - ряд контроля «случайные входы» на те же сутки (тот же порядок и длина, `build_series()["control"]`) или None
                    (тогда (б) не выполнено, кандидатом клетка стать не может);
    n_trades, n_pairs - закрытых сделок и различных пар со сделкой;
    sigma_form    - σ суточной метрики по формированию (§6.6) или None; не конечная или <= 0 - как отсутствующая, с флагом `sigma_form_invalid`;
    mean_without_best_coin - среднее суточных значений без пар лучшего базового актива (условие (в), закрытый вариант) или None → (в) не выполнено;
    mtm_ok        - MTM был посчитан (`build_series()["mtm_available"]`); False → (а) и (д) не выполнены (ряд без MTM основным быть не может, D10);
    closed_days   - закрытый ряд (без MTM) - только справка (`closed_mean/lo/hi`), в статус не входит;
    net_plus1     - точечная оценка основного ряда (среднее суточных значений «закрытые + MTM») при задержке +1 мин на обеих ногах (D11); None → (ж) не выполнено;
    short_trade_share, exchanges - справка отчёта D11 (доля сделок <= 2 мин; биржи ног → `open_semantics`);
    worst_trade   - (индекс суток в ряду, вклад худшей сделки в значение суток) - справка L5-02: нижняя граница без этой сделки;
    n_boot, seed  - бутстреп-t (по умолчанию Н10: 3000 и 37); boot_on_diff - применять бутстреп-t и к (б);
    fee_burden_bps - измеренное суточное бремя комиссий (fee_bps на активную пару-сутки): только справка `mre_over_fee_burden`;
    indist_min_trades - минимум закрытых сделок для «не различимо» (0 = протокол).
    """
    P = _portfolio()
    n = len(cell_days)
    if n == 0:
        raise ValueError("пустой ряд клетки")
    if control_days is not None and len(control_days) != n:
        raise ValueError("ряд контроля не той же длины")
    series = list(cell_days)
    finite = all(_finite(x) for x in series) and (control_days is None or all(_finite(x) for x in control_days))
    sigma_ok = _finite(sigma_form) and sigma_form > 0
    n_eff = n_eff_blocks(n, block)
    res = {"status": UNCLEAR, "label": LABEL_RU[UNCLEAR], "minus": False, "n_days": n, "n_eff": n_eff, "nonfinite_series": not finite,
           "sigma_form_invalid": sigma_form is not None and not sigma_ok, "degenerate": False, "mtm_ok": bool(mtm_ok),
           "skew": None, "diff_skew": None, "mean": None, "lo": None, "hi": None, "p": None, "df": None, "lo_boot": None, "hi_boot": None,
           "diff_mean": None, "diff_lo": None, "diff_hi": None, "diff_lo_boot": None, "diff_hi_boot": None,
           "cond_a": False, "cond_a_t": False, "cond_a_boot": False, "cond_b": False, "cond_b_t": False, "cond_b_boot": False,
           "cond_c": False, "cond_d": False, "cond_e": False, "cond_g": False, "unmet": [],
           "closed_mean": None, "closed_lo": None, "closed_hi": None, "mean_without_best_days": None,
           "mre": None, "mre_consistent": None, "sigma_window": None, "mre_window": None, "mre_over_fee_burden": None,
           "net_plus1": net_plus1, "short_trade_share": short_trade_share, "open_semantics": open_semantics(exchanges) if exchanges else None,
           "worst_trade_value": None, "lo_without_worst_trade": None, "mean_without_worst_trade": None}
    cond_c = mean_without_best_coin is not None and mean_without_best_coin > 0
    cond_d = n_trades >= MIN_TRADES and n_pairs >= MIN_PAIRS
    cond_g = _finite(net_plus1) and net_plus1 > 0
    res.update(cond_c=cond_c, cond_d=cond_d, cond_g=cond_g)
    if not finite:
        res["unmet"] = ["a", "b", "c", "d", "e", "g"]
        return res
    mean, lo, hi, p, df = P.block_t_interval(series, ALPHA, block)
    _, lo_b, hi_b = P.block_boot_t_interval(series, ALPHA, n_boot, seed, block)
    degenerate = lo == hi
    cond_a_t, cond_a_boot = lo > 0 and not degenerate, lo_b > 0 and not degenerate
    cond_a = bool(mtm_ok) and cond_a_t and cond_a_boot
    diff = diff_b = None
    if control_days is None:
        cond_b_t = cond_b_boot = cond_b = False
    else:
        dser = [c - k for c, k in zip(series, control_days)]
        diff = P.block_t_interval(dser, ALPHA, block)
        cond_b_t = diff[1] > 0 and diff[1] != diff[2]
        if boot_on_diff:
            diff_b = P.block_boot_t_interval(dser, ALPHA, n_boot, seed, block)
            cond_b_boot = diff_b[1] > 0 and diff_b[1] != diff_b[2]
        else:
            cond_b_boot = True
        cond_b = cond_b_t and cond_b_boot
        res["diff_skew"] = P.skewness(dser)
    cond_e = bool(mtm_ok) and mean > 0
    mre_val = mre(sigma_form, n_eff) if sigma_ok else None
    contains_zero = lo <= 0.0 <= hi
    indist = (contains_zero and not degenerate and df >= 1 and mre_val is not None and mre_val <= MRE_THRESHOLD_BPS
              and not (empty_unclear and n_trades == 0) and n_trades >= indist_min_trades)
    conds = [("a", cond_a), ("b", cond_b), ("c", cond_c), ("d", cond_d), ("e", cond_e), ("g", cond_g)]
    if all(v for _, v in conds):
        status = CANDIDATE
    elif indist:
        status = INDISTINGUISHABLE
    else:
        status = UNCLEAR
    sd = statistics.stdev(series) if n >= 2 else None
    res.update(status=status, label=LABEL_RU[status], minus=hi < 0.0, mean=mean, lo=lo, hi=hi, p=p, df=df, lo_boot=lo_b, hi_boot=hi_b,
               degenerate=degenerate, skew=P.skewness(series), cond_a=cond_a, cond_a_t=cond_a_t, cond_a_boot=cond_a_boot,
               cond_b=cond_b, cond_b_t=cond_b_t, cond_b_boot=cond_b_boot, cond_e=cond_e, unmet=[k for k, v in conds if not v],
               mean_without_best_days=mean_without_best_days(series), mre=mre_val, mre_consistent=(mre(sigma_form, n_eff, mre_coef_for()) if sigma_ok else None),
               sigma_window=sd, mre_window=(None if sd is None else mre(sd, n_eff)),
               mre_over_fee_burden=(None if mre_val is None or not fee_burden_bps else mre_val / fee_burden_bps))
    if diff is not None:
        res.update(diff_mean=diff[0], diff_lo=diff[1], diff_hi=diff[2])
    if diff_b is not None:
        res.update(diff_lo_boot=diff_b[1], diff_hi_boot=diff_b[2])
    if closed_days is not None and len(closed_days) > 0 and all(_finite(x) for x in closed_days):
        cm, cl, ch, _, _ = P.block_t_interval(list(closed_days), ALPHA, block)
        res.update(closed_mean=cm, closed_lo=cl, closed_hi=ch)
    if worst_trade is not None:
        idx, val = worst_trade
        if 0 <= idx < n and _finite(val):
            s2 = list(series)
            s2[idx] -= val
            m2, l2, _, _, _ = P.block_t_interval(s2, ALPHA, block)
            res.update(worst_trade_value=val, lo_without_worst_trade=l2, mean_without_worst_trade=m2)
    return res


def gate_position_means(closed_gross_sum, closed_fee_sum, n_closed, mtm_gross_sum=None, mtm_fee_sum=None, mtm_n=0):
    """Средние на ПОЗИЦИЮ для ворот entry_z на формировании (D10, L5-01, L6-03): (закрытые + открытые на конце окна по MTM) - не только закрытые.
    Аргументы - суммы bps по клетке: из `--cells-out` (`mtm_gross_bps`, `mtm_fee_bps`, `mtm_n`; ядро запущено с `--mtm-open`) и из сделок
    (Σ gross_bps, Σ fee_bps, число). Возврат (средний валовый, средняя комиссия, число позиций); MTM недоступен (None/NaN) - (nan, nan, n_closed):
    ворота по закрытому варианту читать нельзя, вызывающий обязан отказать. Ворота: средний валовый >= 2 × средняя комиссия при числе позиций
    >= entry_z_min_trades_form (params) - сравнение остаётся в sel_entry.py."""
    if not (_finite(mtm_gross_sum) and _finite(mtm_fee_sum)):
        return float("nan"), float("nan"), n_closed
    n = n_closed + mtm_n
    if n <= 0:
        return float("nan"), float("nan"), 0
    return (closed_gross_sum + mtm_gross_sum) / n, (closed_fee_sum + mtm_fee_sum) / n, n


STATUS_COLUMNS = ["cell", "status", "label", "minus", "n_days", "n_eff", "mean", "lo_t", "hi_t", "lo_boot_t", "hi_boot_t", "skew",
                  "closed_mean", "closed_lo_t", "closed_hi_t", "diff_mean", "diff_lo_t", "diff_lo_boot_t", "diff_skew",
                  "cond_a", "cond_a_t", "cond_a_boot_t", "cond_b", "cond_c", "cond_d", "cond_e", "cond_g", "unmet",
                  "net_plus1", "short_trade_share_le_2min", "open_semantics", "mre", "mre_coef_2_487", "sigma_window", "mre_window",
                  "mre_over_fee_burden", "lo_without_worst_trade", "worst_trade_value", "degenerate", "sigma_form_invalid", "nonfinite_series"]


def status_row(cell, res):
    """Строка таблицы статусов (колонки STATUS_COLUMNS) из результата `classify`; None печатается пустым полем, булевы - 0/1, `unmet` - через «+»."""
    src = {"cell": cell, "lo_t": res["lo"], "hi_t": res["hi"], "lo_boot_t": res["lo_boot"], "hi_boot_t": res["hi_boot"],
           "closed_lo_t": res["closed_lo"], "closed_hi_t": res["closed_hi"], "diff_lo_t": res["diff_lo"], "diff_lo_boot_t": res["diff_lo_boot"],
           "cond_a_boot_t": res["cond_a_boot"], "short_trade_share_le_2min": res["short_trade_share"], "mre_coef_2_487": res["mre_consistent"]}
    row = []
    for c in STATUS_COLUMNS:
        v = src[c] if c in src else res[c]
        if isinstance(v, bool):
            v = int(v)
        elif isinstance(v, (list, tuple)):
            v = "+".join(map(str, v))
        row.append("" if v is None else v)
    return row


# ------------------------------------------------------------------ ожидаемое число ложных, M и M_эфф (D9)
def expected_false_candidates(m_cells, size=0.05):
    """Ожидаемое число ложных «кандидатов» при нуле: size·M. size - измеренный размер правила (size_test.py), номинал 0,05;
    клетки зависимы (общий рынок, общие активы) - число ориентир, не оценка (P-37 §10.4)."""
    return size * m_cells


def expected_false_range(m_cells, m_eff, size=0.05):
    """Диапазон ожидаемого числа ложных (D9): от size·M_эфф до size·M (клетки зависимы; верхний конец - для независимых)."""
    lo, hi = size * m_eff, size * m_cells
    return (min(lo, hi), max(lo, hi))


def m_distinct(series_by_cell, ndigits=9):
    """M без дублей (D9): клетки с тождественным суточным рядом (например, рука a1, у которой ни один стоп не сработал, равна руке a0) считаются один раз.
    series_by_cell: {имя: [значения]} → число различных рядов (сравнение по значениям, округление до ndigits знаков)."""
    return len({tuple(round(x, ndigits) for x in v) for v in series_by_cell.values()})


def m_eff(matrix):
    """M_эфф (P-37 §10.4): коэффициент участия (Σλ)²/Σλ² собственных чисел λ матрицы корреляций рядов клеток. Для корреляционной матрицы Σλ = M (след),
    Σλ² = Σ_ij c_ij² (норма Фробениуса), поэтому M_эфф = M²/Σ c_ij² без разложения. matrix - T×M (numpy; строки - наблюдения: часы, для классов
    с сессией сутки); столбцы с нулевой дисперсией отбрасываются. Оценка занижена при T < M (в шуме выборочных корреляций M_эфф ≈ T·M/(T+M)): ориентир, не оценка.
    Возврат (M_эфф, число использованных столбцов)."""
    import numpy as np
    x = np.asarray(matrix, dtype=float)
    keep = np.isfinite(x).all(axis=0) & (x.std(axis=0) > 0)
    x = x[:, keep]
    m = x.shape[1]
    if m == 0 or x.shape[0] < 2:
        return float("nan"), m
    c = np.corrcoef(x, rowvar=False)
    c = np.atleast_2d(c)
    return float(m * m / (c * c).sum()), m
