#!/usr/bin/env python3
"""T-32 boot (возврат Судьи 27.09, п.3) — парный круговой блочный бутстреп по суткам для двух денежных
кандидатов, выбранных как лучшие внутри своей сетки: epcap.onePerCoin («одна сделка на монету в эпизоде»,
сетка `data/t32/epcap-summary.md`, просмотрено N=12 вариантов) и exits.g103_pause30 («пауза 30 мин»,
сетка `data/t32/exits-summary.md`, просмотрено N=18 вариантов). Оба варианта — main-trades.csv минус
отброшенные правилом сделки (ничего не меняют, только убирают), поэтому суточная разность $ = минус сумма
pnl отброшенных сделок этих суток (по t1, UTC).

Единица бутстрепа — сутки месяца (аналог П-02 R2, `p02-r2-read.py`): круговой блочный бутстреп,
b = ceil(n^(1/3)) (август n=31 -> b=4, сентябрь n=23 -> b=3), 20000 повторов, seed 20260927; p = 2*min(доля
<=0, доля >=0). Поправка «лучший из N» — Бонферрони по числу вариантов, из которых кандидат выбран лучшим
(верхняя граница: P(хотя бы один из N шумовых вариантов пройдёт порог) <= N * P(один вариант пройдёт)):
p_adj = min(1, N * p_boot).

    python tools/compute/t32-boot.py --out data/t32/boot.json --summary data/t32/boot-summary.md
"""
import argparse
import datetime as dt
import importlib.util
import json
import math
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
MONTHS = {"aug": ("2026-08", 31), "sep": ("2026-09", 23)}
N_GRID = {"onePerCoin": ("epcap", 12), "g103_pause30": ("exits", 18)}
REPS_DEFAULT = 20000
SEED_DEFAULT = 20260927


def _mod(fn):
    spec = importlib.util.spec_from_file_location(os.path.basename(fn)[:-3], os.path.join(HERE, fn))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def day_key(ms_):
    return dt.datetime.fromtimestamp(ms_ / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def day_list(pref, n):
    return [f"{pref}-{i:02d}" for i in range(1, n + 1)]


def daily_diff(dropped, pref, n):
    """dropped — сделки (dict с t1, pnl), выпавшие из главного варианта. Возвращает (units, out_of_range)."""
    days = day_list(pref, n)
    agg = {d: 0.0 for d in days}
    out_of_range = 0
    for t in dropped:
        d = day_key(t["t1"])
        if d in agg:
            agg[d] -= t["pnl"]
        else:
            out_of_range += 1
    return [agg[d] for d in days], out_of_range


def block_boot(units, b, reps, seed):
    n = len(units)
    rng = random.Random(seed)
    k = math.ceil(n / b)
    out = []
    for _ in range(reps):
        sample = []
        for _ in range(k):
            s = rng.randrange(n)
            sample.extend(units[(s + j) % n] for j in range(b))
        out.append(sum(sample[:n]))
    out.sort()
    lo, hi = out[int(0.025 * len(out))], out[int(0.975 * len(out)) - 1]
    p = 2 * min(sum(v <= 0 for v in out), sum(v >= 0 for v in out)) / len(out)
    return lo, hi, min(1.0, p)


def read(units, reps, seed):
    n = len(units)
    b = math.ceil(n ** (1 / 3))
    est = sum(units)
    lo, hi, p = block_boot(units, b, reps, seed)
    return {"n_days": n, "b": b, "est": round(est, 2), "ci95": [round(lo, 2), round(hi, 2)], "p_boot": round(p, 4)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trades", default="data/t32/main-trades.csv")
    ap.add_argument("--reps", type=int, default=REPS_DEFAULT)
    ap.add_argument("--seed", type=int, default=SEED_DEFAULT)
    ap.add_argument("--out", default="data/t32/boot.json")
    ap.add_argument("--summary", default="data/t32/boot-summary.md")
    a = ap.parse_args()

    ep = _mod("t32-epcap.py")
    ex = _mod("t32-exits.py")

    trades_ep = ep.load_trades(a.trades)
    regime = ep.load_regime(ep.REGIME_DIRS)
    episodes = ep.build_episodes(regime)
    ep_of = {id(t): ep.assign_episode(t["t0"], episodes) for t in trades_ep}
    one_per_coin = ep.variant_one_per_coin(trades_ep, ep_of)
    kept_ids = {id(t) for t in one_per_coin}
    dropped_epcap = [{"t1": t["t1"], "pnl": t["pnl"]} for t in trades_ep if id(t) not in kept_ids]

    trades_ex = ex.load_trades(a.trades)
    kept30, _ = ex.variant_pause(trades_ex, 30, only_stop=False)
    kept_ids2 = {id(t) for t in kept30}
    dropped_pause = [{"t1": t["t1"], "pnl": ex.pnl_of(t)} for t in trades_ex if id(t) not in kept_ids2]

    candidates = {"onePerCoin": dropped_epcap, "g103_pause30": dropped_pause}

    res = {}
    for name, dropped in candidates.items():
        grid, N = N_GRID[name]
        res[name] = {"grid": grid, "n_variants_viewed": N, "n_dropped_trades": len(dropped), "months": {}}
        for mon, (pref, n) in MONTHS.items():
            units, oor = daily_diff(dropped, pref, n)
            r = read(units, a.reps, a.seed)
            r["out_of_range_trades"] = oor
            r["p_adj_best_of_N"] = round(min(1.0, r["p_boot"] * N), 4)
            res[name]["months"][mon] = r

    json.dump(res, open(a.out, "w", encoding="utf-8", newline=""), ensure_ascii=False, indent=1)
    write_summary(a.summary, res, a.reps, a.seed)
    print(open(a.summary, encoding="utf-8").read())


def write_summary(path, res, reps, seed):
    lines = ["# T-32 boot — парный блочный бутстреп по суткам для «лучших из N» кандидатов "
             "(возврат Судьи 27.09, п.3)\n"]
    lines.append("Разность = pnl(варианта) − pnl(главного) по суткам (UTC, t1); вариант только убирает сделки "
                  "главного, поэтому разность суток = минус сумма pnl отброшенных сделок этих суток.\n")
    lines.append("| кандидат | сетка (N просмотрено) | месяц | сделок отброшено | Δ$ (сумма по месяцу) | "
                  "95% интервал (блочный бутстреп) | p | p·N («лучший из N») |")
    lines.append("|---|---|---|---|---|---|---|---|")
    names_ru = {"aug": "август", "sep": "сентябрь"}
    for name, r in res.items():
        for mon, rr in r["months"].items():
            sig = "держится (0 вне интервала)" if rr["ci95"][0] > 0 or rr["ci95"][1] < 0 else "накрывает 0"
            lines.append(f"| {name} | {r['grid']} (N={r['n_variants_viewed']}) | {names_ru[mon]} | "
                         f"{r['n_dropped_trades']} | {rr['est']:+.2f} | [{rr['ci95'][0]:+.2f}; {rr['ci95'][1]:+.2f}] "
                         f"({sig}) | {rr['p_boot']:.4f} | {rr['p_adj_best_of_N']:.4f} |")
    lines.append("")
    verdicts = []
    for name, r in res.items():
        both_hold = all(r["months"][m]["ci95"][0] > 0 for m in ("aug", "sep"))
        both_sig_adj = all(r["months"][m]["p_adj_best_of_N"] < 0.05 for m in ("aug", "sep"))
        v = "улучшение держится и после поправки на N" if (both_hold and both_sig_adj) else \
            "не проходит с поправкой на N (интервал по суткам широк и/или p·N >= 0,05 в одном из месяцев) — не улучшение"
        verdicts.append(f"{name}: {v}")
    lines.append("Вывод: " + "; ".join(verdicts) + ".")
    lines.append("")
    lines.append(f"Метод: круговой блочный бутстреп по суткам, b = ⌈n^(1/3)⌉ (август n=31 → b=4, сентябрь n=23 → "
                 f"b=3), {reps} повторов, seed {seed}; p = 2·min(доля ⩽ 0, доля ⩾ 0). Поправка «лучший из N» — "
                 "Бонферрони (верхняя граница вероятности, что хотя бы один из N просмотренных в этой сетке "
                 "вариантов случайно прошёл бы порог): p·N, N — из `epcap-summary.md`/`exits-summary.md` "
                 "(«просмотрено вариантов»). Никакой t-статистики по сделкам; занятость монеты и очередь входа не "
                 "пересчитаны (как в исходных сетках).")
    open(path, "w", encoding="utf-8", newline="").write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
