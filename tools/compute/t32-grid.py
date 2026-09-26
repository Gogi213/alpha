#!/usr/bin/env python3
"""T-32: две ручки владельца вместе (27.09 ~04:55: «хедж биткоина и ограничение позиций — как скажется на просадках и
перехаях»). Сетка на готовых сделках главного (`data/t32/main-trades.csv`) без нового бэктеста:

    хедж      {0; 50 %; полная бета} × инструмент {BTC; ETH (T-33); корзина пула}  — PnL хеджа по сделке из `data/t32/hedge-trades.csv`
    ограничение {нет; одновременно 3/5/8; на эпизод BTC первые 3/5/8} — отбор сделок из `t32-epcap.py`

По клетке и месяцу: KPI «до перехая» (самый долгий с хвостом, p90 по времени, новых максимумов), просадка по закрытиям,
$, сделок. Отбор сделок не зависит от хеджа; убранные сделки выпадают, остальные не пересчитываются (занятость монеты и
очередь — оговорка). Всё на днях подбора.

    python tools/compute/t32-grid.py --out data/t32/grid.json --summary data/t32/grid-summary.md
"""
import argparse
import csv
import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def _mod(name, fn):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fn))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


HEDGES = [("без хеджа", None), ("BTC 50 %", "hedge_btc_50"), ("BTC полная β", "hedge_btc_100"),
          ("ETH 50 %", "hedge_eth_50"), ("ETH полная β", "hedge_eth_100"),
          ("корзина 50 %", "hedge_basket_50"), ("корзина полная β", "hedge_basket_100")]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trades", default="data/t32/main-trades.csv")
    ap.add_argument("--hedge", default="data/t32/hedge-trades.csv")
    ap.add_argument("--out", default="data/t32/grid.json")
    ap.add_argument("--summary", default="data/t32/grid-summary.md")
    a = ap.parse_args()
    ep = _mod("epcap", "t32-epcap.py")
    kn = ep.load_kn()
    episodes = ep.build_episodes(ep.load_regime(ep.REGIME_DIRS))
    trades = ep.load_trades(a.trades)
    hedge = {(r["sym"], int(r["t0_ms"])): r for r in csv.DictReader(open(a.hedge, encoding="utf-8"))}
    cols = set(next(iter(hedge.values())).keys())
    hedges = [(h, c) for h, c in HEDGES if c is None or c in cols]  # ETH (T-33, отложена) — только если посчитан
    miss = [t for t in trades if (t["sym"], t["t0"]) not in hedge]
    assert not miss, f"нет хеджа для {len(miss)} сделок"
    ep_of = {id(t): ep.assign_episode(t["t0"], episodes) for t in trades}
    limits = [("нет", list(trades))]
    limits += [(f"одновременно {n}", ep.variant_cap(trades, n)) for n in (3, 5, 8)]
    limits += [(f"на эпизод {n}", ep.variant_first_n(trades, ep_of, n)) for n in (3, 5, 8)]
    res = {}
    for hname, col in hedges:
        for lname, sel in limits:
            xs = [{**t, "pnl": t["pnl"] + (float(hedge[(t["sym"], t["t0"])][col]) if col else 0.0)} for t in sel]
            m = ep.metrics_for(kn, xs)
            res[f"{hname} | {lname}"] = {"hedge": hname, "limit": lname, **{
                pk: {"worst_d": round(v["hours"]["worst"] / 24, 1), "tw_p90_h": round(v["hours"]["tw_p90"] or 0),
                     "n_high": v["hours"]["n"], "dd_usd": v["dd_usd"], "usd": v["usd"], "n": v["n"]} for pk, v in m.items()}}
    json.dump(res, open(a.out, "w", encoding="utf-8", newline=""), ensure_ascii=False, indent=1)
    ok = [k for k, r in res.items() if r["aug"]["worst_d"] <= 5 and r["sep"]["worst_d"] <= 5]
    L = [f"# T-32: хедж × ограничение позиций — сетка на готовых сделках главного", "",
         f"≤ 5 дней до перехая в обоих месяцах: {', '.join(ok) if ok else 'нет клеток'}. Клеток {len(res)}; на днях подбора.", "",
         "## Теплокарта: до перехая, дни — худший из августа и сентября (✓ — ≤ 5 в обоих)", "",
         "| хедж \\ ограничение | " + " | ".join(l for l, _ in limits) + " |", "|---" * (len(limits) + 1) + "|"]
    for hname, _ in hedges:
        cells = []
        for lname, _ in limits:
            r = res[f"{hname} | {lname}"]
            w = max(r["aug"]["worst_d"], r["sep"]["worst_d"])
            cells.append(f"{w:.1f}".replace(".", ",") + (" ✓" if w <= 5 else ""))
        L.append(f"| {hname} | " + " | ".join(cells) + " |")
    L += ["", "## Все клетки (август ; сентябрь)", "",
          "| хедж | ограничение | до перехая, дн | p90 по времени, ч | новых максимумов | просадка $ | $ | сделок |", "|---" * 8 + "|"]
    for k, r in res.items():
        f = lambda key, fmt="{}": f"{fmt.format(r['aug'][key])} ; {fmt.format(r['sep'][key])}"
        L.append(f"| {r['hedge']} | {r['limit']} | {f('worst_d')} | {f('tw_p90_h')} | {f('n_high')} | {f('dd_usd', '{:.0f}')} | "
                 f"{f('usd', '{:+.0f}')} | {f('n')} |")
    L += ["", "Оговорки: хедж — по закрытиям сделки (без переоценки внутри), маржа хеджа не учтена; ограничения — отбор из "
          "готовых сделок, занятость монеты и очередь не пересчитаны; «одновременно N» — по [t0; t1] сделок главного."]
    open(a.summary, "w", encoding="utf-8", newline="").write("\n".join(L) + "\n")
    print("\n".join(L[:12]))


if __name__ == "__main__":
    main()
