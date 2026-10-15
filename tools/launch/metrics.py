"""TK-244, В-9: метрики хеша по closes psim — PnL $, Шарп, винрейт, перехай. Чистые функции, без ввода-вывода (читает closes раннер
снимка Летописи на calc, не браузер). Определения (подпись страницы и docs/findings/tk244-metrics-2026-10-15.md):
  счёт — cap 0 (без потолка позиций), как pnl_usd Летописи и сито TK-237; кривая счёта — Σ$ закрытий с нуля, без депозита;
  Шарп — mean/std(ddof=1) суточного $ по календарным суткам UTC всех месяцев хеша (сутки без сделок = 0), как сито TK-237
    (tools/compute/tk237-sieve.py), НЕ годовой; закрытия после конца месяца (В-94) идут продолжением суток, не отбрасываются;
  винрейт — доля закрытий с $ > 0;
  перехай (В-120, kpi-newhigh.periods_to_high) — по каждому месяцу с нуля: самый долгий период «максимум → новый максимум» в
    сутках (старт месяца = максимум 0, новый максимум — счёт строго выше прежних), незакрытый хвост (последний максимум → конец
    месяца) отдельно; по хешу — худший месяц. Цель ≤ 5 дн (В-120, доп. 3) — с хвостом: worst = max(период, хвост).
Нет сделок / нет месяцев — None (на странице прочерк, не «0»)."""
import datetime
import math

MS_DAY = 86_400_000
GOAL_DAYS = 5.0   # В-120, доп. 3


def month_bounds(mo):
    """'2026-04' -> (мс начала, мс начала следующего), UTC."""
    y, m = map(int, mo.split("-"))
    a = datetime.datetime(y, m, 1, tzinfo=datetime.timezone.utc)
    b = datetime.datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=datetime.timezone.utc)
    return int(a.timestamp() * 1000), int(b.timestamp() * 1000)


def periods_to_high(events, start, end):
    """events: [(t, $)] по времени -> (закрытые периоды, хвост, число новых максимумов); t/start/end в одних единицах."""
    cum, peak, t_peak, out = 0.0, 0.0, start, []
    for t, p in events:
        cum += p
        if cum > peak + 1e-9:
            out.append(t - t_peak)
            peak, t_peak = cum, t
    return out, max(end, events[-1][0] if events else end) - t_peak, len(out)


def _sharpe(day):
    n = len(day)
    if n < 2:
        return None
    mean = sum(day) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in day) / (n - 1))
    return mean / sd if sd > 1e-12 else None


def hash_metrics(closes_by_month):
    """{месяц: [[мс, $, монета?], …]} (cap 0) -> {n, pnl, winrate, sharpe, overhigh_d, tail_d, worst_d, months, by_month}.
    Месяц с пустым списком = результат есть, сделок нет (в Шарп входит нулевыми сутками). Нет ни одной сделки — None во всех полях метрик."""
    day, n, wins, pnl, by_month = [], 0, 0, 0.0, {}
    for mo in sorted(closes_by_month):
        a, b = month_bounds(mo)
        ev = sorted((t, usd) for t, usd, *_ in closes_by_month[mo])
        nd = max((b - a) // MS_DAY, ((ev[-1][0] - a) // MS_DAY + 1) if ev else 0)
        d = [0.0] * nd
        for t, usd in ev:
            d[(t - a) // MS_DAY] += usd
        day += d
        n += len(ev)
        wins += sum(1 for _, usd in ev if usd > 0)
        pnl += sum(usd for _, usd in ev)
        if ev:
            per, tail, _ = periods_to_high(ev, a, b)
            by_month[mo] = {"n": len(ev), "pnl": round(sum(u for _, u in ev), 2),
                            "overhigh_d": round(max(per, default=0) / MS_DAY, 2), "tail_d": round(tail / MS_DAY, 2)}
        else:
            by_month[mo] = {"n": 0, "pnl": 0.0, "overhigh_d": None, "tail_d": None}
    if not n:
        return {"n": 0, "pnl": None, "winrate": None, "sharpe": None, "overhigh_d": None, "tail_d": None, "worst_d": None,
                "months": len(closes_by_month), "by_month": by_month}
    oh = max(v["overhigh_d"] for v in by_month.values() if v["n"])
    tl = max(v["tail_d"] for v in by_month.values() if v["n"])
    sh = _sharpe(day)
    return {"n": n, "pnl": round(pnl, 2), "winrate": round(wins / n, 4), "sharpe": None if sh is None else round(sh, 4),
            "overhigh_d": oh, "tail_d": tl, "worst_d": max(oh, tl), "months": len(closes_by_month), "by_month": by_month}


def best_by_sharpe(per_hash):
    """Метрики лучшего варианта прогона по Шарпу: {хеш: hash_metrics} -> (хеш, метрики) | (None, None); хеши без Шарпа не участвуют."""
    cand = [(m["sharpe"], h) for h, m in per_hash.items() if m and m.get("sharpe") is not None]
    if not cand:
        return None, None
    _, h = max(cand)
    return h, per_hash[h]
