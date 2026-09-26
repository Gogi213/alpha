#!/usr/bin/env python3
"""Сборка одного JSON для нового дашборда «Лонг в просадке» (план 2026-09-25) из уже
собранных `titration-dashboard-data.py` файлов (сентябрь/август/обвал) и сводок
`portfolio-sim.py` (защиты счёта). Считает готовые для отрисовки числа один раз здесь —
страница только показывает, ничего не пересчитывает по-другому.

Вход — фиксированные файлы `data/titration-dashboard/` (не в git):
    titration-dashboard-u500r-cases.json   сентябрь (история 01–15.09 + запись 16–23.09)
    titration-dashboard-aug-u500r.json     август (форма cand/tr05/h4 по 3 наборам)
    titration-dashboard-crash-u500r.json   обвал 10–11.10.2025 (те же формы)
    protection-dash-notrx.json             счёт $2500 (portfolio-sim, один прогон): эпохи «сентябрь», «август»,
                                           «август+сентябрь» (один счёт подряд с 01.08), «обвал»; 4 варианта
                                           страницы; `--drop` прогона (монеты вне торгового пула, В-105) — и
                                           сделки этих монет убираются со страницы, чтобы счёт и сделки совпадали

Период «август + сентябрь» — сделки августа и сентября подряд; счёт — отдельный непрерывный прогон, не сумма двух.

Выход: --out data/titration-dashboard/data-merged.json → на вход titration-dashboard-build.py.

    titration-dashboard-merge.py --in-dir data/titration-dashboard --out data/titration-dashboard/data-merged.json
"""
import argparse
import json
import os
import re
import statistics as st

# 4 варианта страницы: ключ → (набор, форма-суффикс из titration-dashboard-data.py). Первый — главный (В-104).
VARIANTS = [
    ("btc4h_trail", "просадка BTC за 4 ч, трейл 1 % / откат 1 %", "t-bid-btc4h-q1", "cand"),
    ("btc4h_take", "просадка BTC за 4 ч, тейк 1,75 %", "t-bid-btc4h-q1", "h4"),
    ("cand", "просадка BTC за 1 ч, трейл 1 % / откат 1 %", "t-bid-btc1h-q1", "cand"),
    ("nofilter", "без фильтра просадки (база)", "t-bid-age-45", "cand"),
]
# Имя варианта в protection-dash-notrx.json (portfolio-sim `--variant`).
ACCOUNT_VARIANT_NAME = {
    "btc4h_trail": "BTC 4 ч: трейл 1/1",
    "btc4h_take": "BTC 4 ч: тейк 1,75 %",
    "cand": "Кандидат: трейл 1/1",
    "nofilter": "Без фильтра просадки",
}
# Гипотезы П-02 вариантами (владелец 26.09 ~23:20: «все гипотезы надо с деньгами, пнл, винрейтом и т. д.») — справочно:
# варианты на тех же днях подбора, не вердикт (вердикт — отчёты П-02). Ключ → (кнопка, подпись, имя в portfolio-sim,
# ключ сделок в --extra файлах titration-dashboard-data.py, оговорка). Нет сделок ни в одном периоде — вариант не
# показывается. Обвал для них не считался.
P02_VARIANTS = [
    ("g105", "Главный + стоп перед стеной (Г-105)", "Г-105: стоп перед стеной вместо 2 %, остальное как у главного",
     "Г-105 стоп перед стеной", "t-bid-btc4h-q1/h10", None),
    ("g85", "Главный + вход от фронтрана (Г-85)", "Г-85: вход одной заявкой вплотную к фронтрану вместо лестницы, остальное как у главного",
     "Г-85 вход от фронтрана", "t-bid-btc4h-q1/h7", None),
    ("g86", "BTC 4 ч, стена ≥ 45 мин, съедена ≥ 89,9 % — рыночный вход на касании (Г-86)", "Г-86: рыночный вход на касании, когда стену проели ≥ 89,9 % (сигнал касания, не подхода)",
     "Г-86 рыночный вход", "t-bid-btc4h-q1/h9",
     "Лимитной стороны нет: на сигнале касания лимитная заявка всегда пересекает спред и отклоняется — сравнивать не с чем. "
     "В сентябре таких касаний почти не было (1 сделка)."),
    ("g28", "Г-28 сильная стена", "Г-28: только сделки у сильной стены (верхняя треть: уровни рядом + фронтран)",
     "Г-28 сильная стена", "f-g28/main", None),
    ("g28w", "Г-28 слабая стена", "Г-28 обратная: только сделки у слабой стены (нижняя треть) — направление, которое показал счёт",
     "Г-28 слабая стена", "f-g28w/main", None),
    ("g08", "Главный, только стена на круглом числе (Г-08)", "Г-08: только сделки, где стена на круглом числе (≥ 2 нулей в цене)",
     "Г-08 круглое число", "f-g08/main",
     "Мало сделок: у круглых стен август 9, сентябрь 7 — по такому числу вывод не делается."),
    ("g33", "Г-33 без декоративных", "Г-33: без сделок у декоративной стены (цепочка перестановок ≥ 2 без торговли)",
     "Г-33 без декоративных", "f-g33/main", None),
    ("g07", "Г-07 глубина позади", "Г-07: только сделки, где позади стены глубокий стакан (верхняя треть)",
     "Г-07 глубина позади", "f-g07/main", None),
    ("g46", "Г-46 поток против цены", "Г-46: только сделки, где поток тейкеров за 60 с шёл против движения цены",
     "Г-46 поток против цены", "f-g46/main", None),
    ("g36", "Г-36 айсберг", "Г-36: только сделки у стены-айсберга (прошлые касания проторговали ≥ её видимого размера)",
     "Г-36 айсберг", "f-g36/main", None),
]
# Этапы сделки главного варианта (В-104) и что меняет каждая гипотеза — таблица «что изменено» на странице
# (CEO 27.09: владелец не понял, что гипотеза = главный + одна замена).
MAIN_STAGES = [
    ("сигнал", "цена подошла к бид-стене на 20 bps"),
    ("фильтр", "BTC за 4 ч упал на 44,55 bps и больше; стене ≥ 45 мин, в ней ≥ $10 000"),
    ("вход", "лестница: 3 лимитные заявки от 2 до 20 bps над стеной (ближняя — половина объёма), живут 30 мин"),
    ("стоп", "2 % ниже цены входа"),
    ("выход", "трейл: включается на +1 %, закрывает при откате на 1 %; не дольше 4 ч"),
]
P02_CHANGES = {
    "g105": {"стоп": "на 1 тик над стеной — выход, как только цену пустили к стене"},
    "g85": {"вход": "одна заявка вплотную к фронтрану (у самой стены), без лестницы"},
    "g86": {"сигнал": "цена коснулась стены", "фильтр": "то же + стену уже проели на 89,9 % и больше",
            "вход": "рыночная заявка в момент касания"},
    "g08": {"фильтр": "то же + цена стены круглая (≥ 2 нулей на конце)"},
    "g28": {"фильтр": "то же + стена сильная: верхняя треть «уровни рядом + фронтран» на момент входа"},
    "g28w": {"фильтр": "то же + стена слабая: нижняя треть «уровни рядом + фронтран» на момент входа"},
    "g33": {"фильтр": "то же + без декоративных стен (цепочка из ≥ 2 перестановок без торговли)"},
    "g07": {"фильтр": "то же + позади стены глубокий стакан (верхняя треть) на момент входа"},
    "g46": {"фильтр": "то же + за 60 с до входа поток тейкеров шёл против движения цены"},
    "g36": {"фильтр": "то же + стена-айсберг: прошлые касания проторговали не меньше её видимого размера"},
}
# Гипотезы П-02 без денежного варианта — одной строкой, почему.
P02_UNDEFINED = [
    ("Г-88 вход после закола", "закол обнуляет возраст стены, а главный вариант берёт только стены старше 45 минут — сделок нет по построению"),
    ("Г-85 × Г-105 вместе", "0 сделок: стоп перед стеной отклоняет план, когда заявка фронтрана стоит в 1 тике от стены (почти всегда)"),
]
# Сигнальная гипотеза без сделок в --extra: признак П-02 мерился на касании стены, а главный вариант входит на подходе —
# у 83 % сделок касания нет (замер `p02-variant-filter.py link`, 27.09); деньгами — после решения владельца.
P02_ABSENT_WHY = ("признак мерился на касании стены, а главный вариант входит на подходе (касание есть лишь у 17 % сделок) — "
                  "пересчитывается на момент входа (решение владельца В-117): Г-36, Г-33, Г-46 — счёт, Г-28 и Г-07 — после кода")
P02_NOTE = "Справочно: вариант посчитан на тех же днях, на которых проверялась гипотеза, — это не вердикт (вердикт — в разделе «Гипотезы П-02»)."
# Период страницы → эпоха portfolio-sim.
PERIOD_EPOCH = {"sep": "сентябрь", "aug": "август", "augsep": "август+сентябрь", "crash": "обвал"}


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def cash(net_bps, usd, position_usd):
    u = usd if usd is not None else position_usd
    return net_bps / 1e4 * u


def trade_rows(doc, set_name, form_key, position_usd, drop=frozenset()):
    """Строки form_trades[<set>/<form_key>] → компактные списки без имени эпохи (она не
    нужна: период один на файл, суточная граница подэпох — в periods); монеты `drop` — вне пула."""
    key = f"{set_name}/{form_key}"
    rows = (doc.get("form_trades") or {}).get(key) or []
    out = []
    for r in rows:
        # columns: epoch, day, symbol, t0_min, exit_min, net_bps, control_bps, reason, fill_frac, usd
        _epoch, day, sym, t0, exit_, net, ctrl, reason, fill, usd = r
        if sym in drop:
            continue
        out.append([day, sym, int(t0), int(exit_), round(net, 2), round(ctrl, 2), reason, fill,
                    round(usd, 2) if usd is not None else None])
    return out


R_DAY, R_SYM, R_T0, R_EXIT, R_NET, R_CTRL, R_REASON, R_FILL, R_USD = range(9)


def day_bucket_usd(rows, position_usd):
    by_day = {}
    for r in rows:
        by_day[r[R_DAY]] = by_day.get(r[R_DAY], 0.0) + cash(r[R_NET], r[R_USD], position_usd)
    return by_day


def sweep_open(rows, position_usd):
    """Открытые позиции по минутам (вход..выход) — пик числа и пик $ в рынке, доля времени
    с хотя бы одной открытой позицией (оценка сверху: вход/выход в пределах своей минуты)."""
    ev = []
    for r in rows:
        u = r[R_USD] if r[R_USD] is not None else position_usd
        ev.append((r[R_T0], 1, u))
        ev.append((r[R_EXIT], -1, u))
    if not ev:
        return {"peak_n": 0, "peak_usd": 0.0, "open_minutes": 0}
    ev.sort(key=lambda x: (x[0], -x[1]))
    open_n = open_usd = 0
    peak_n = peak_usd = 0
    open_minutes = 0
    prev_t = None
    for t, d, u in ev:
        if prev_t is not None and open_n > 0:
            open_minutes += max(0, t - prev_t)
        open_n += d
        open_usd += d * u
        if open_n > peak_n:
            peak_n, peak_usd = open_n, open_usd
        elif open_n == peak_n and open_usd > peak_usd:
            peak_usd = open_usd
        prev_t = t
    return {"peak_n": peak_n, "peak_usd": round(peak_usd, 2), "open_minutes": open_minutes}


def trade_stats(rows, position_usd, deposit_usd, span_minutes):
    n = len(rows)
    if not n:
        return None
    wins = [r for r in rows if r[R_NET] > 0]
    losses = [r for r in rows if r[R_NET] <= 0]
    net_usd = sum(cash(r[R_NET], r[R_USD], position_usd) for r in rows)
    avg_bps = sum(r[R_NET] for r in rows) / n
    avg_win_bps = sum(r[R_NET] for r in wins) / len(wins) if wins else 0.0
    avg_loss_bps = sum(r[R_NET] for r in losses) / len(losses) if losses else 0.0
    gw = sum(r[R_NET] for r in wins)
    gl = -sum(r[R_NET] for r in losses)
    pf = (gw / gl) if gl > 0 else None
    win_rate = len(wins) / n
    hold_min = sum(r[R_EXIT] - r[R_T0] for r in rows) / n
    by_day = day_bucket_usd(rows, position_usd)
    sweep = sweep_open(rows, position_usd)
    # просадка по цепочке закрытий сделок (не минутная переоценка счёта — грубее account.dd_pct)
    cum = peak = dd = 0.0
    for r in sorted(rows, key=lambda r: r[R_EXIT]):
        cum += cash(r[R_NET], r[R_USD], position_usd)
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    reasons = {}
    for r in rows:
        rr = reasons.setdefault(r[R_REASON], {"n": 0, "usd": 0.0, "win": 0})
        rr["n"] += 1
        rr["usd"] += cash(r[R_NET], r[R_USD], position_usd)
        if r[R_NET] > 0:
            rr["win"] += 1
    for rr in reasons.values():
        rr["usd"] = round(rr["usd"], 2)
        rr["win_share"] = round(rr["win"] / rr["n"], 3)
    return {
        "n": n, "net_usd": round(net_usd, 2), "avg_usd": round(net_usd / n, 2),
        "avg_bps": round(avg_bps, 2), "avg_win_bps": round(avg_win_bps, 2),
        "avg_loss_bps": round(avg_loss_bps, 2), "win_rate": round(win_rate, 4),
        "profit_factor": round(pf, 3) if pf is not None else None,
        "hold_min": round(hold_min, 1), "dd_usd_trade": round(dd, 2),
        "dd_pct_trade": round(-dd / deposit_usd * 100, 3) if deposit_usd else None,
        "peak_n": sweep["peak_n"], "peak_usd": sweep["peak_usd"],
        "pct_time_in_market": round(sweep["open_minutes"] / span_minutes, 4) if span_minutes else None,
        "days_pos": sum(1 for v in by_day.values() if v > 0), "days_total": len(by_day),
        "reasons": reasons, "daily_usd": {k: round(v, 2) for k, v in by_day.items()},
    }


def sharpe_sortino(daily_pct, annualize=365):
    vals = list(daily_pct.values())
    if len(vals) < 2:
        return None, None
    mean = st.mean(vals)
    std = st.pstdev(vals)
    sharpe = (mean / std * (annualize ** 0.5)) if std > 0 else None
    downside = [v for v in vals if v < 0]
    dstd = st.pstdev(downside) if len(downside) >= 2 else (abs(downside[0]) if len(downside) == 1 else 0)
    sortino = (mean / dstd * (annualize ** 0.5)) if dstd else None
    return (round(sharpe, 2) if sharpe is not None else None,
            round(sortino, 2) if sortino is not None else None)


def account_row(grid, variant_name, epoch_name, max_pos, day_stop, kill, exclude_name):
    for g in grid:
        if (g["variant"] == variant_name and g["epoch"] == epoch_name and g["max_pos"] == max_pos
                and g["day_stop"] == day_stop and g["kill"] == kill and g["exclude"] == exclude_name
                and g.get("streak_stop", 0) == 0):
            return g
    return None


def account_summary(g, deposit_usd, span_minutes=None):
    if g is None:
        return None
    # T-20 п.4 (26.09): средние и профит-фактор — по сделкам, которые счёт реально взял (нет полей —
    # прогон `portfolio-sim` старее правки, тогда None и страница берёт прежние значения kpi)
    pf = g.get("profit_factor")
    om = g.get("open_minutes")
    extra = {
        "avg_usd": round(g["avg_usd"], 2) if "avg_usd" in g else None,
        "avg_bps": round(g["avg_bps"], 2) if "avg_bps" in g else None,
        "avg_win_bps": round(g["avg_win_bps"], 2) if "avg_win_bps" in g else None,
        "avg_loss_bps": round(g["avg_loss_bps"], 2) if "avg_loss_bps" in g else None,
        "profit_factor": round(pf, 3) if pf is not None else None,
        "pct_time_in_market": round(om / span_minutes, 4) if (om is not None and span_minutes) else None,
    }
    daily_pct = {d: v / deposit_usd * 100 for d, v in g["daily"].items()}
    sharpe, sortino = sharpe_sortino(daily_pct)
    return {
        "n": g["n"], "net_usd": round(g["total_usd"], 2), "net_pct": round(g["total_pct"], 3),
        "dd_usd": round(g["dd_usd"], 2), "dd_pct": round(g["dd_pct"], 3),
        "recovery_factor": round(g["rf"], 3) if g.get("rf") is not None else None,
        "win_rate": round(g["win"], 4), "peak_n": g["peak_n"], "peak_usd": round(g["peak_usd"], 2),
        "worst_day": g["worst_day"], "worst_day_usd": round(g["worst_day_usd"], 2),
        "worst_day_pct": round(g["worst_day_pct"], 3), "fill": round(g["fill"], 4),
        "sharpe": sharpe, "sortino": sortino, "daily_usd": g["daily"],
        **extra,
    }


AGG_RE = re.compile(r"pct([0-9.]+)-(tk[0-9.]+|tr[0-9.]+x[0-9.]+|1to1)-(\d+)-ttl1800(-eat20)?$")
SET_KEY = {"t-bid-btc1h-q1": "btc1h_q1", "t-bid-btc4h-q1": "btc4h_q1", "t-bid-age-45": "age_45"}


def build_exit_heat(exit_agg, epoch_names):
    """Тепловая карта «стоп × тейк» и таблица трейлинга (4 ч, без реакции на стену) —
    сумма $ и средняя bps по обеим эпохам сентября, из сводки exit-titration-read.py."""
    hist_name, rec_name = epoch_names
    heat, trail = {}, {}
    for row in exit_agg:
        m = AGG_RE.search(row["form"])
        if not m:
            continue
        set_key = SET_KEY.get(row["set"])
        if not set_key:
            continue
        stop, take, dl, wall = float(m.group(1)), m.group(2), int(m.group(3)) // 3600, bool(m.group(4))

        def num(prefix, field):
            v = row.get(f"{prefix}_{field}")
            return None if v in (None, "") else float(v)

        n_h, n_r = num(hist_name, "n") or 0, num(rec_name, "n") or 0
        n = n_h + n_r
        if n == 0:
            continue
        usd_h, usd_r = num(hist_name, "usd") or 0, num(rec_name, "usd") or 0
        net_h, net_r = num(hist_name, "net"), num(rec_name, "net")
        net_avg = ((net_h or 0) * n_h + (net_r or 0) * n_r) / n if n else None
        entry = {"n": int(n), "usd": round(usd_h + usd_r, 2), "net_bps": round(net_avg, 2) if net_avg is not None else None}
        if dl == 4 and not wall and take.startswith("tk"):
            heat.setdefault(set_key, []).append({"stop": stop, "take_pct": round(float(take[2:]), 2), **entry})
        if dl == 4 and not wall and take.startswith("tr"):
            act, gap = take[2:].split("x")
            trail.setdefault(set_key, []).append({"stop": stop, "act_pct": float(act), "gap_pct": float(gap), **entry})
    return heat, trail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-dir", default="data/titration-dashboard")
    ap.add_argument("--protection", default="protection-dash-notrx.json")
    ap.add_argument("--extra", action="append", default=[],
                    help="период=файл titration-dashboard-data.py со сделками вариантов П-02 (sep|aug), повторяемый")
    ap.add_argument("--extra-protection", action="append", default=[],
                    help="прогон portfolio-sim.py --json с вариантами П-02 (те же эпохи), повторяемый")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    d = a.in_dir
    sep = load(os.path.join(d, "titration-dashboard-u500r-cases.json"))
    aug = load(os.path.join(d, "titration-dashboard-aug-u500r.json"))
    crash = load(os.path.join(d, "titration-dashboard-crash-u500r.json"))
    prot = load(os.path.join(d, a.protection))
    # Одна правда о пуле: монеты вне торгового пула берутся из прогона счёта — сделки страницы и счёт совпадают.
    drop = frozenset(prot.get("drop") or [])
    extra_trades = {"sep": {}, "aug": {}}
    for spec in a.extra:
        pkey, path = spec.split("=", 1)
        extra_trades[pkey].update(load(path).get("form_trades") or {})
    for path in a.extra_protection:
        ep = load(path)
        assert sorted(ep.get("drop") or []) == sorted(drop), f"{path}: другой --drop, чем у основного прогона"
        prot["grid"] = prot["grid"] + ep["grid"]
    p02 = [v for v in P02_VARIANTS if any(v[4] in extra_trades[p] for p in extra_trades)]

    position_usd, deposit_usd = sep["position_usd"], sep["deposit_usd"]
    hist_ep, rec_ep = sep["epochs"][0]["name"], sep["epochs"][1]["name"]
    sep_boundary = rec_ep and sep["epochs"][1]["from"]

    out = {
        "generated_utc": sep["generated_utc"], "position_usd": position_usd, "deposit_usd": deposit_usd,
        "points": sep["points"], "pool": [c for c in (sep.get("pool") or []) if c not in drop], "drop": sorted(drop),
        "variants": [{"key": k, "label": lbl} for k, lbl, _, _ in VARIANTS] +
                    [{"key": k, "label": lbl, "pill": pill, "group": "p02", "note": note}
                     for k, pill, lbl, _acc, _tk, note in p02],
        "p02_undefined": [{"h": h, "why": why} for h, why in P02_UNDEFINED] +
                         ([{"h": ", ".join(dict.fromkeys(v[3].split()[0] for v in absent)), "why": P02_ABSENT_WHY}]
                          if (absent := [v for v in P02_VARIANTS if v not in p02 and v[4].startswith("f-")]) else []),
        "p02_note": P02_NOTE, "main_stages": MAIN_STAGES,
        "p02_changes": {k: P02_CHANGES.get(k, {}) for k, *_ in p02},
        "periods": [
            {"key": "sep", "label": "Сентябрь", "from": sep["epochs"][0]["from"], "to": sep["epochs"][1]["to"],
             "boundary": sep_boundary, "tuned_from": "2026-09-16", "tuned_to": "2026-09-20",
             "days": sep["days"]},
            {"key": "aug", "label": "Август", "from": aug["epochs"][0]["from"], "to": aug["epochs"][0]["to"],
             "days": aug["days"]},
            {"key": "augsep", "label": "Август + сентябрь", "from": aug["epochs"][0]["from"], "to": sep["epochs"][1]["to"],
             "boundary": sep_boundary, "tuned_from": "2026-09-16", "tuned_to": "2026-09-20",
             "days": aug["days"] + sep["days"]},
            {"key": "crash", "label": "Обвал 10–11.10.2025", "from": crash["epochs"][0]["from"],
             "to": crash["epochs"][0]["to"], "days": crash["days"]},
        ],
        "trades": {}, "kpi": {}, "account": {}, "exit_heat": {}, "protections": {},
        "exit_agg_status": {"sep": "ready", "aug": "ready"},
    }

    # точная длина периода в минутах — по календарным суткам от from до to включительно
    import datetime as dt
    def minutes_span(frm, to):
        d0 = dt.datetime.strptime(frm, "%Y-%m-%d")
        d1 = dt.datetime.strptime(to, "%Y-%m-%d") + dt.timedelta(days=1)
        return int((d1 - d0).total_seconds() // 60)
    span_minutes = {p["key"]: minutes_span(p["from"], p["to"]) for p in out["periods"]}

    for pkey, doc in {"sep": sep, "aug": aug, "crash": crash}.items():
        out["trades"][pkey] = {vkey: trade_rows(doc, set_name, form_key, position_usd, drop)
                               for vkey, _label, set_name, form_key in VARIANTS}
    for pkey in ("sep", "aug"):
        for vkey, _pill, _lbl, _acc, tkey, _note in p02:
            out["trades"][pkey][vkey] = trade_rows({"form_trades": extra_trades[pkey]}, *tkey.split("/", 1), position_usd, drop)
    vkeys = [v[0] for v in VARIANTS] + [v[0] for v in p02]
    out["trades"]["augsep"] = {vkey: out["trades"]["aug"][vkey] + out["trades"]["sep"][vkey] for vkey in vkeys}
    for pkey in PERIOD_EPOCH:
        out["kpi"][pkey] = {vkey: trade_stats(out["trades"][pkey].get(vkey) or [], position_usd, deposit_usd, span_minutes[pkey])
                            for vkey in vkeys}
    account_names = {**ACCOUNT_VARIANT_NAME, **{v[0]: v[3] for v in p02}}

    # счёт (portfolio-sim) и защиты — по каждому периоду и варианту, из одного прогона
    def protection_steps(grid, variant_name, epoch_name):
        steps = [
            ("Без защит", dict(max_pos=0, day_stop=0.0, kill=0.0, exclude_name="нет")),
            ("+ выключатель BTC −1,5 %/1 ч и исключение прокидов", dict(max_pos=0, day_stop=0.0, kill=150.0, exclude_name="прокиды")),
            ("+ потолок 3 позиции", dict(max_pos=3, day_stop=0.0, kill=150.0, exclude_name="прокиды")),
        ]
        out_rows = []
        for label, c in steps:
            g = account_row(grid, variant_name, epoch_name, **c)
            s = account_summary(g, deposit_usd)
            out_rows.append({"label": label, **(s or {})})
        return out_rows if any("n" in r for r in out_rows) else None

    combo = dict(max_pos=0, day_stop=0.0, kill=0.0, exclude_name="нет")
    for pkey, epoch in PERIOD_EPOCH.items():
        out["account"][pkey] = {vkey: account_summary(account_row(prot["grid"], aname, epoch, **combo), deposit_usd,
                                                       span_minutes.get(pkey))
                                for vkey, aname in account_names.items()}
        out["protections"][pkey] = {vkey: protection_steps(prot["grid"], aname, epoch)
                                    for vkey, aname in account_names.items()}

    heat_sep, trail_sep = build_exit_heat(sep.get("exit_agg") or [], (hist_ep, rec_ep))
    out["exit_heat"]["sep"] = {"heat": heat_sep, "trail": trail_sep}

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{a.out}: {os.path.getsize(a.out) / 1e6:.2f} МБ")


if __name__ == "__main__":
    main()
