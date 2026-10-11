#!/usr/bin/env python3
"""TK-022: ворота счёта янв–июн — клетки p07m-main / p07b-base / p07a-h2-fr1, посчитанные на VPS, против посчитанных на деке
(28.09, p07-h1) на тех же сутках: rounds.csv без строк `#` побайтно. Печать — только ключи и ok/РАЗНИЦА.
    python3 tk022-gate.py [ref-каталог]   (на VPS; ref = /home/deck/alpha/tk022/gate-ref/<эпоха>/b5/<клетка>/<сутки>/<набор>)
"""
import os
import sys

REF = sys.argv[1] if len(sys.argv) > 1 else "/home/deck/alpha/tk022/gate-ref"
HOME = "/home/deck/alpha/epochs"
SET = "t-bid-btc4h-q1"


def body(p):
    return [l for l in open(p, encoding="utf-8").read().splitlines() if not l.startswith("#")]


ok_all, n, miss = True, 0, 0
for ep in sorted(os.listdir(REF)):
    for cell in sorted(c for c in os.listdir(f"{REF}/{ep}/b5") if not c.startswith(".")):
        for day in sorted(os.listdir(f"{REF}/{ep}/b5/{cell}")):
            ref = f"{REF}/{ep}/b5/{cell}/{day}/{SET}/rounds.csv"
            got = f"{HOME}/{ep}/b5/{cell}/{day}/{SET}/rounds.csv"
            if not (os.path.exists(ref) and os.path.exists(got) and os.path.exists(f"{HOME}/{ep}/b5/{cell}/{day}/.done")):
                miss += 1
                continue
            same = body(ref) == body(got)
            n += 1
            ok_all &= same
            print(f"{ep}/{cell}/{day}: {'ok' if same else 'РАЗНИЦА'} ({len(body(ref)) - 1} строк)")
print(f"ВОРОТА СЧЁТА: сверено {n}, ещё не посчитано на VPS {miss} — {'ok' if ok_all else 'НЕ ПРОЙДЕНЫ'}")
sys.exit(0 if ok_all else 1)
