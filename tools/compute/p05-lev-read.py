#!/usr/bin/env python3
"""П-05 §3а: позиции $500…$2500 (множитель размера `portfolio-sim --size-mult` 1/2/3/5) — деньги, просадка, пик позиций,
фактическое плечо и запас до ликвидации, по месяцам. Линейно, **без пересчёта очереди**.

Запас до ликвидации (кросс-маржа, один депозит на все позиции, как `portfolio-sim`): доля движения цены против всех
открытых на пике позиций разом, при которой капитал падает до поддерживающей маржи: x = (D − mmr·P) / P, P — пик $ в
рынке. mmr — первый уровень Bybit (`/v5/market/risk-limit`, снято 27.09, файл `--mmr`): медиана пула и максимум пула.

    python tools/compute/p05-lev-read.py --dir data/p05 --mmr data/p05/bybit-risk-limit-tier1-2026-09-27.json \
        --out docs/findings/p05-leverage-2026-09-27.md
"""
from __future__ import annotations

import argparse
import json
import os

MULTS = (1, 2, 3, 5)
MONTHS = ("август", "сентябрь")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--mmr", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    mm = sorted(v["mmr"] for v in json.load(open(a.mmr, encoding="utf-8")).values() if "mmr" in v)
    mmr_med, mmr_max = mm[len(mm) // 2], mm[-1]
    rows = {}
    dep = 2500.0
    for m in MULTS:
        p = json.load(open(os.path.join(a.dir, f"protection-p05-lev-x{m}.json"), encoding="utf-8"))
        dep = p.get("deposit_usd") or dep
        for g in p["grid"]:
            if g["max_pos"] or g["kill"] or g["exclude"] != "нет" or g["day_stop"] or g["epoch"] not in MONTHS:
                continue
            P = g["peak_usd"]
            rows.setdefault(g["variant"], {}).setdefault(g["epoch"], {})[m] = {
                "usd": g["total_usd"], "dd": g["dd_pct"], "n": g["n"], "peak_n": g["peak_n"], "peak_usd": P,
                "lev": P / dep, "liq_med": (dep - mmr_med * P) / P * 100 if P else None,
                "liq_max": (dep - mmr_max * P) / P * 100 if P else None, "stress": g.get("stress_pct")}
    L = ["# П-05 §3а: позиция $500…$2500 — деньги, просадка, плечо, запас до ликвидации", "",
         f"Счёт $2500, без защит, **линейно, без пересчёта очереди** (исполнение как у позиции $500). Запас до ликвидации — "
         f"движение цены против всех открытых на пике позиций разом до поддерживающей маржи (кросс): при mmr медианы пула "
         f"{mmr_med * 100:.1f} % и максимума пула {mmr_max * 100:.0f} % (Bybit, первый уровень). Стоп сделки — 2 %.", ""]
    for v, byM in rows.items():
        L += [f"## {v}", "", "| позиция | месяц | $ | просадка | сделок | пик позиций | пик $ в рынке | плечо | запас до ликв. (медиана / макс mmr) |",
              "|---|---|---:|---:|---:|---:|---:|---:|---|"]
        for m in MULTS:
            for mo in MONTHS:
                r = byM.get(mo, {}).get(m)
                if not r:
                    continue
                L.append(f"| ${500 * m} | {mo} | {r['usd']:+.0f} | −{r['dd']:.1f} % | {r['n']} | {r['peak_n']} | "
                         f"${r['peak_usd']:,.0f} | ×{r['lev']:.1f} | {r['liq_med']:.0f} % / {r['liq_max']:.0f} % |".replace(",", " "))
        L.append("")
    open(a.out, "w", encoding="utf-8", newline="").write("\n".join(L))
    json.dump(rows, open(a.out.rsplit(".", 1)[0] + ".json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
