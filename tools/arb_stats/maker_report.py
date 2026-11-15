"""Сводка счёта «мейкер + тейкер-хедж» (maker_run.py): итоги по настройкам и по упорядоченным парам бирж, топ пар по $ в сутки.

Запуск: python3 maker_report.py <result.pkl> <выход.json>
Настройка (cfg) = (индекс θ, L мс, вариант закрытия «tt» тейкер-тейкер / «mt» мейкер-закрытие).
Для топа: $ в сутки по всему окну и отдельно по первой/второй половине окна (отбор по всему окну — подгонка; половины показывают, держится ли).
"""
import json
import pickle
import sys
from collections import defaultdict

import numpy as np

import maker


def pq(x, q):
    return float(np.percentile(x, q)) if len(x) else None


def half_usd(r, lo, hi, day_frac):
    """$ в сутки по кругам, открытым в [lo, hi), закрытым."""
    k = (r[:, 0] >= lo) & (r[:, 0] < hi) & ~np.isnan(r[:, 5])
    return float((r[k, 4] * r[k, 5] / 1e4).sum() / day_frac)


def usd_trim(r, day_frac):
    """$ в сутки без самого выгодного круга (устойчивость к одному выбросу)."""
    k = ~np.isnan(r[:, 5])
    v = r[k, 4] * r[k, 5] / 1e4
    return float((v.sum() - (v.max() if len(v) else 0.0)) / day_frac)


def main(pkl, out):
    d = pickle.load(open(pkl, "rb"))
    t0, t1 = d["t0"], d["t1"]
    mid = (t0 + t1) // 2
    win_days = (t1 - t0) / 86_400_000
    half_days = win_days / 2
    cfgs = sorted({k for v in d["rows"].values() for k in v}, key=lambda c: (c[2], c[1], c[0]))
    agg, byex, tops, tops_bx, agg_bx, groups, tops_core = {}, {}, {}, {}, {}, {}, {}
    for cfg in cfgs:
        allrows = [(k, v[cfg]) for k, v in d["rows"].items() if cfg in v]
        # хедж на BingX считается по книге-снимку ≈ 2,6/с: цена хеджа устаревшая → оценка завышена; в основной итог и топ не входит
        grp = {"M=BingX": [kv for kv in allrows if kv[0][1] == "bingx"], "H=BingX": [kv for kv in allrows if kv[0][2] == "bingx"],
               "без BingX": [kv for kv in allrows if "bingx" not in kv[0][1:]]}
        groups[str(cfg)] = {g: {"pairs": len(v), "pairs_pos": int(sum(1 for _, s_ in v if s_["usd_day"] > 0)), "rounds": int(sum(s_["n"] for _, s_ in v)),
                                "usd_day": float(sum(s_["usd_day"] for _, s_ in v))} for g, v in grp.items()}
        rows = [kv for kv in allrows if kv[0][2] != "bingx"]
        rows_bx = [kv for kv in allrows if kv[0][2] == "bingx"]
        agg_bx[str(cfg)] = {"pairs": len(rows_bx), "usd_day_total": float(sum(s["usd_day"] for _, s in rows_bx)), "rounds": int(sum(s["n"] for _, s in rows_bx))}
        pooled = [d["rounds"][k][cfg] for k, _ in rows if cfg in d["rounds"].get(k, {})]
        allr = np.concatenate(pooled) if pooled else np.zeros((0, len(maker.COLS)))
        cl = allr[~np.isnan(allr[:, 5])]
        usd = np.array([s["usd_day"] for _, s in rows])
        agg[str(cfg)] = {"pairs": len(rows), "pairs_with_rounds": int(sum(1 for _, s in rows if s["n"])), "rounds": int(len(allr)), "closed": int(len(cl)),
                         "unclosed": int(sum(s["unclosed"] for _, s in rows)), "usd_day_total": float(usd.sum()),
                         "pairs_pos": int((usd > 0).sum()), "pairs_neg": int((usd < 0).sum()),
                         "net_med": pq(cl[:, 5], 50), "net_p95": pq(cl[:, 5], 95), "pos": float((cl[:, 5] > 0).mean()) if len(cl) else None,
                         "slip_med": pq(allr[:, 3], 50), "slip_p95": pq(allr[:, 3], 95), "hold_med_s": pq(cl[:, 6] / 1000, 50),
                         "edge_med": pq(allr[:, 2], 50)}
        g = defaultdict(lambda: [0, 0.0, 0])
        for (coin, m_, h_), s in rows:
            x = g[(m_, h_)]
            x[0] += s["n"]
            x[1] += s["usd_day"]
            x[2] += 1
        byex[str(cfg)] = {f"{m_}>{h_}": {"rounds": x[0], "usd_day": x[1], "pairs": x[2]} for (m_, h_), x in sorted(g.items())}
        top = []
        for (coin, m_, h_), s in sorted(rows, key=lambda kv: -kv[1]["usd_day"])[:40]:
            r = d["rounds"].get((coin, m_, h_), {}).get(cfg)
            top.append({"coin": coin, "M": m_, "H": h_, **s, "usd_1": half_usd(r, t0, mid, half_days) if r is not None else None,
                        "usd_2": half_usd(r, mid, t1, half_days) if r is not None else None,
                        "usd_trim": usd_trim(r, win_days) if r is not None else None})
        tops[str(cfg)] = top
        core_rows = [kv for kv in allrows if "bingx" not in kv[0][1:]]
        tops_core[str(cfg)] = [{"coin": c, "M": m_, "H": h_, **s_, "usd_trim": usd_trim(d["rounds"][(c, m_, h_)][cfg], win_days) if cfg in d["rounds"].get((c, m_, h_), {}) else None}
                               for (c, m_, h_), s_ in sorted(core_rows, key=lambda kv: -kv[1]["usd_day"])[:10]]
        tops_bx[str(cfg)] = [{"coin": c, "M": m_, "H": h_, **s} for (c, m_, h_), s in sorted(rows_bx, key=lambda kv: -kv[1]["usd_day"])[:10]]
    doc = {"t0": t0, "t1": t1, "win_h": (t1 - t0) / 3600_000, "model": d["model"], "agg": agg, "agg_bingx_hedge": agg_bx, "groups": groups, "byex": byex, "top": tops, "top_bingx_hedge": tops_bx, "top_core": tops_core,
           "mismatch": len(d["mismatch"]), "pairs_total": len(d["rows"])}
    json.dump(doc, open(out, "w"), ensure_ascii=False)
    print(json.dumps({"cfgs": len(cfgs), "pairs": len(d["rows"])}))
    for c in cfgs:
        a = agg[str(c)]
        print(c, {k: (round(v, 2) if isinstance(v, float) else v) for k, v in a.items()})


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
