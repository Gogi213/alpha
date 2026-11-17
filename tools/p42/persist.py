"""П-42: персистентность аутсайдеров («100 %») — биномиальный p и поправка Бонферрони по outsiders-summary.json (описательно, не испытания).

Выбор «100 %»-монет по одной половине; в другой половине k из n выбранных (со сделками там) — в минусе по сумме клеток; нулевая доля — доля убыточных
среди всех монет со сделками в этой половине (`base_share_neg_other_half`). Односторонний p = P(X ≥ k), X ~ Bin(n, доля). «Вместе» — сумма k и n по двум
направлениям при средневзвешенной по n доле. Бонферрони — ×M (M = стороны 2 × движки 2 × направления 2 = 8 проверок).
"""
import json
import os
from math import comb

M_CHECKS = 8


def binom_sf(k, n, p):
    return sum(comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))


def rows(summary_path):
    OS = json.load(open(summary_path, encoding="utf-8"))
    out = []
    for side in ("long", "short"):
        for eng in ("1m", "tick"):
            r = {"side": side, "engine": eng}
            kt = nt = 0
            wsum = 0.0
            for d in ("A->B", "B->A"):
                v = OS["%s|%s|%s|100" % (side, eng, d)]
                k, n, p0 = v["other_net_neg"], v["with_trades_other_half"], v["base_share_neg_other_half"]
                r[d] = dict(selected=v["selected"], n=n, k=k, p0=p0, p=binom_sf(k, n, p0) if n else None)
                kt, nt, wsum = kt + k, nt + n, wsum + n * p0
            p0t = wsum / nt if nt else None
            r["both"] = dict(n=nt, k=kt, p0=p0t, p=binom_sf(kt, nt, p0t) if nt else None)
            for key in ("A->B", "B->A", "both"):
                if r[key]["p"] is not None:
                    r[key]["p_bonf"] = min(1.0, r[key]["p"] * M_CHECKS)
            out.append(r)
    return out


if __name__ == "__main__":
    import sys
    for r in rows(sys.argv[1]):
        print(r["side"], r["engine"], {k: (round(v["p"], 4) if v["p"] is not None else None) for k, v in r.items() if isinstance(v, dict)},
              {k: (v["k"], v["n"], round(v["p0"], 3)) for k, v in r.items() if isinstance(v, dict)})
