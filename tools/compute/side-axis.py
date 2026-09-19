#!/usr/bin/env python3
"""Ось стороны (этап 1 docs/plan/alpha-roadmap-2026-09-19.md): бид-стены (лонг) против аск-стен (шорт)
по семьям флоров — из четырёх сеток `lob bounce-grid --side` и их вердиктов.

    python3 tools/compute/side-axis.py --prefix b5/side-2026-09-19 --verdict-dir study \\
            [--family a45 --family s100] [--touches study/touches] [--top 4] [--out side-axis.md]

Печатает markdown: (1) семья × сторона — вердикт (лучшая форма, n, точка/нижняя, DSR, итог);
(2) по семье — те же формы на обеих сторонах (n, сумма, на круг) и по суткам, рядом медианная
дневная доходность монет пула по касаниям (`--touches`: первое и последнее касание монеты за
сутки — режим дня, рост/падение); (3) по монетам у лучшей формы каждой стороны. Интервалов
по монете и по суткам не строит — это срез, вердикт даёт `lob bounce-verdict`.
"""
import argparse
import collections
import csv
import glob
import os
import re
import statistics
import sys
from importlib import import_module

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ft = import_module("family-table")


def load_forms(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(l for l in f if not l.startswith("#")))


def by_form(rows):
    """form → [n_fills, sum_net, n_signals, {day: [fills, sum]}, {symbol: [fills, sum]}]."""
    out = {}
    for r in rows:
        f = r["form"]
        d = out.setdefault(f, [0.0, 0.0, 0.0, collections.defaultdict(lambda: [0.0, 0.0]),
                               collections.defaultdict(lambda: [0.0, 0.0])])
        n, s, sig = float(r["n_fills"] or 0), float(r["sum_net_bps"] or 0), float(r["n_signals"] or 0)
        d[0] += n
        d[1] += s
        d[2] += sig
        d[3][r["day_utc"]][0] += n
        d[3][r["day_utc"]][1] += s
        d[4][r["symbol"]][0] += n
        d[4][r["symbol"]][1] += s
    return out


def day_returns(touches_dir):
    """Медианная дневная доходность монет пула по касаниям: последнее/первое касание монеты за сутки
    (в тиках — отношение не зависит от шага цены); монета считается, если касания покрывают > 6 ч."""
    out = {}
    for day_dir in sorted(glob.glob(os.path.join(touches_dir, "*"))):
        day = os.path.basename(day_dir)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            continue
        rets = []
        for path in glob.glob(os.path.join(day_dir, "touches-*.csv")):
            first = last = None
            with open(path, encoding="utf-8") as f:
                for r in csv.DictReader(l for l in f if not l.startswith("#")):
                    t, p = int(r["start_ms"]), int(r["price_tick"])
                    if first is None or t < first[0]:
                        first = (t, p)
                    if last is None or t > last[0]:
                        last = (t, p)
            if first and last and first[1] > 0 and last[0] - first[0] > 6 * 3600 * 1000:
                rets.append(last[1] / first[1] - 1.0)
        if rets:
            out[day] = (statistics.median(rets) * 100.0, len(rets), sum(1 for x in rets if x > 0))
    return out


def per_round(n, s):
    return f"{s / n:+.1f}" if n else "—"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--prefix", required=True, help="b5/side-<день> — каталоги <prefix>-<семья>-<сторона>")
    p.add_argument("--verdict-dir", default="study")
    p.add_argument("--family", action="append", help="семья (умолчание a45 и s100)")
    p.add_argument("--touches", help="study/touches — режим дня по касаниям")
    p.add_argument("--top", type=int, default=4, help="форм на семью в таблице сторон")
    p.add_argument("--min-fills", type=int, default=5)
    p.add_argument("--out")
    a = p.parse_args()
    fams = a.family or ["a45", "s100"]
    label = os.path.basename(a.prefix)
    lines = []

    # (1) вердикты
    lines += [f"### Вердикты сеток `{label}-<семья>-<сторона>`", "",
              "| семья | сторона | лучшая форма | кругов / сигналов | точка / нижняя, bps | DSR | итог |",
              "|---|---|---|---|---|---|---|"]
    grids = {}
    for fam in fams:
        for side in ("bid", "ask"):
            gdir = f"{a.prefix}-{fam}-{side}"
            vpath = os.path.join(a.verdict_dir, f"bounce-verdict-{label}-{fam}-{side}.csv")
            v = ft.parse_verdict(vpath) if os.path.exists(vpath) else None
            fpath = os.path.join(gdir, "forms.csv")
            grids[(fam, side)] = (by_form(load_forms(fpath)) if os.path.exists(fpath) else {}, v)
            if v:
                lines.append(f"| {fam} | {side} | `{v['form']}` | {v['fills']} / {v['signals']} | "
                             f"{v['point']} / {v['lower']} | {v['dsr']} | {v['result']} |")
            else:
                lines.append(f"| {fam} | {side} | — | — | — | — | вердикта нет ({vpath}) |")
    lines.append("")

    regime = day_returns(a.touches) if a.touches else {}
    days = sorted({d for (bf, _) in grids.values() for f in bf.values() for d in f[3]})
    if regime:
        lines += ["### Режим дня по касаниям (медианная дневная доходность монет пула)", "",
                  "| сутки | медиана, % | монет | из них вверх |", "|---|---|---|---|"]
        for d in days:
            if d in regime:
                m, n, up = regime[d]
                lines.append(f"| {d} | {m:+.2f} | {n} | {up} |")
        lines.append("")

    # (2) стороны на одних формах
    for fam in fams:
        bid, vb = grids[(fam, "bid")]
        ask, va = grids[(fam, "ask")]
        if not bid and not ask:
            continue
        picks = []
        for v in (vb, va):
            if v and v["form"] and v["form"] not in picks:
                picks.append(v["form"])
        for f in sorted(bid, key=lambda f: bid[f][1], reverse=True):
            if len(picks) >= a.top:
                break
            if f not in picks:
                picks.append(f)
        lines += [f"### Семья `{fam}`: одна форма — две стороны (по суткам: сумма bps (кругов))", "",
                  "| форма | бид (лонг): n · сумма · на круг | по суткам | аск (шорт): n · сумма · на круг | по суткам |",
                  "|---|---|---|---|---|"]
        for f in picks:
            cells = []
            for side_forms in (bid, ask):
                x = side_forms.get(f)
                if not x:
                    cells += ["—", "—"]
                    continue
                cells.append(f"{x[0]:.0f} · {x[1]:+.0f} · {per_round(x[0], x[1])}")
                cells.append(" / ".join(f"{d[5:]}: {x[3][d][1]:+.0f} ({x[3][d][0]:.0f})" for d in days))
            lines.append(f"| `{f}` | {cells[0]} | {cells[1]} | {cells[2]} | {cells[3]} |")
        lines.append("")
        for side, forms in (("bid", bid), ("ask", ask)):
            pos = sum(1 for x in forms.values() if x[1] > 0)
            lines.append(f"- `{fam}-{side}`: форм в плюсе по пулу {pos} из {len(forms)}; "
                         f"кругов по всем формам {sum(x[0] for x in forms.values()):.0f}")
        lines.append("")

    # (3) по монетам у лучшей формы каждой стороны
    for fam in fams:
        for side in ("bid", "ask"):
            forms, v = grids[(fam, side)]
            if not v or v["form"] not in forms:
                continue
            x = forms[v["form"]]
            coins = sorted(x[4].items(), key=lambda kv: kv[1][1], reverse=True)
            thick = [(s, c) for s, c in coins if c[0] >= a.min_fills]
            pos = sum(1 for _, c in thick if c[1] > 0)
            top = ", ".join(f"{s[:-4]} {c[1]:+.0f} ({c[0]:.0f})" for s, c in coins[:6])
            bot = ", ".join(f"{s[:-4]} {c[1]:+.0f} ({c[0]:.0f})" for s, c in coins[-6:][::-1])
            lines.append(f"- `{fam}-{side}` `{v['form']}`: монет с кругами {len(coins)}, с ≥ {a.min_fills} — "
                         f"{len(thick)} (в плюсе {pos}); лучшие: {top}; худшие: {bot}")
    text = "\n".join(lines)
    print(text)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
