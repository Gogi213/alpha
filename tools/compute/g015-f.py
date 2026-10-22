#!/usr/bin/env python3
"""TK-355 (G-015) ручка F: безубыточная комиссия на ногу f* из готовых таблиц TK-326 (rounds B1 fut А/С, спот S0), без прохода.
f* = gross/2 (maker+maker, обе ноги на номинал входа). Две базы: среднее по сделке (gross bps) и по обороту (Σ gross$ / оборот).
Печатает рядом тариф фьюча (а) из тех же таблиц: (Σ gross$ − Σ$(а)) / оборот / 2 на ногу. Месяцы отдельно, n в каждой строке."""
import re, sys, os
D = "docs/findings"
SRC = [("fut A (B1, принятая)", "tk326-tables-futB1-A-2026-10-20.md"),
       ("fut C (общие, сверка)", "tk326-tables-futB1-common-2026-10-20.md"),
       ("spot S0", "tk326-tables-spot-2026-10-20.md")]
out = []
for name, fn in SRC:
    t = open(os.path.join(D, fn), encoding="utf-8").read()
    h = re.search(r"n = (\d+), дней (\d+); Σ\$ \(а\) (-?\d+) .*?Σ gross\$ до издержек (-?\d+); оборот Σ qty·px (\d+) \$; средний gross (-?[\d.]+) bps", t)
    n, days, sa, sg, turn, mg = int(h[1]), int(h[2]), int(h[3]), int(h[4]), int(h[5]), float(h[6])
    sec = t.split("### 4. Месяц", 1)[1].split("###", 1)[0]
    rows = [r.strip("| ").split("|") for r in sec.splitlines() if r.startswith("| 2026")]
    fee_leg = (sg - sa) / turn * 1e4 / 2
    out.append(f"\n### {name}: n={n}, суток {days}; gross по сделке {mg:+.2f} бп, по обороту {sg/turn*1e4:+.2f} бп/круг; "
               f"f* (по обороту) {sg/turn*1e4/2:+.2f} бп/ногу; тариф (а) модели {fee_leg:.2f} бп/ногу\n")
    out.append("| месяц | n | gross бп/сделку | f* бп/ногу (gross/2) | gross − круг тарифа (а) | знак |\n|---|---|---|---|---|---|")
    pos = 0
    for r in rows:
        r = [x.strip() for x in r]
        g = float(r[3]); nn = int(r[1])
        net = g - 2 * fee_leg
        pos += net > 0
        out.append(f"| {r[0]} | {nn} | {g:+.2f} | {g/2:+.2f} | {net:+.2f} | {'+' if net>0 else '−'} |")
    out.append(f"\nмесяцев с (gross − круг тарифа (а)) > 0: {pos} из {len(rows)}")
print("\n".join(out))
