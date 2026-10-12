"""Г-18: доля комиссий/проскальзывания в валовой доходности кругов B1 (описание без Холма, в m не входит; KIND report: rounds.csv месяца).
Валовая = dir*(exit_px-entry_vwap)/entry_vwap в bps; издержки = валовая - net_bps (комиссии обеих ног + всё, что вычел прогон). Строки по причине выхода
и «все»: n, сумма валовой и издержек в bps и в $ (по номиналу qty*entry_vwap), издержки на круг, доля издержек в |валовой|."""
NAME = "g18_fee_share"
KIND, SOURCES = "report", ()
PARAMS = {"off": (0,)}


def run(data, ctx, off):
    acc = {}
    for r in data:
        ev, xp, nb = float(r["entry_vwap"]), float(r["exit_px"]), float(r["net_bps"])
        g = int(r["dir"]) * (xp - ev) / ev * 1e4
        notional = float(r["qty"]) * ev
        for k in (r["reason"], "all"):
            a = acc.setdefault(k, [0, 0.0, 0.0, 0.0, 0.0])
            a[0] += 1; a[1] += g; a[2] += g - nb; a[3] += g * notional / 1e4; a[4] += (g - nb) * notional / 1e4
    return [{"reason": k, "n": a[0], "gross_bps_sum": round(a[1], 3), "cost_bps_sum": round(a[2], 3), "cost_bps_per_round": round(a[2] / a[0], 4),
             "gross_usd": round(a[3], 2), "cost_usd": round(a[4], 2), "cost_share_of_abs_gross": round(a[4] / abs(a[3]), 4) if a[3] else ""}
            for k, a in sorted(acc.items())]
