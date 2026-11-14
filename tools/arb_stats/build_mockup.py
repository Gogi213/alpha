"""Собирает HTML-макет вкладки «Арбитраж»: result-e.json + detail-e.pkl (этап B) + снимок /api/v3 + страница здоровья page.html.

Запуск: python build_mockup.py <result.json> <detail.pkl> <v3.json> <page.html> <шаблон> <выход.html> [<result-rx.json> <detail-rx.pkl>]
"""
import json
import pickle
import re
import sys

import numpy as np

sys.path.insert(0, __import__("pathlib").Path(__file__).parent.as_posix())
import core  # noqa: E402

NAMES = {"binance": "Binance", "bybit": "Bybit", "okx": "OKX", "bingx": "BingX"}
TOP_1MIN = 40


def r1(v):
    return None if v is None or (isinstance(v, float) and not np.isfinite(v)) else round(float(v), 1)


def bars_tf(b, tf):
    """Склеивает минутные бары до tf минут. -> {t, S, B, c} (значения ×10, целые)."""
    mins = b["m0"] + b["mins"]
    g = mins // tf
    idx = np.flatnonzero(np.r_[True, g[1:] != g[:-1]])
    end = np.r_[idx[1:], len(mins)]

    def one(a):
        o = a[0][idx]
        h = np.maximum.reduceat(a[1], idx)
        lo = np.minimum.reduceat(a[2], idx)
        c = a[3][end - 1]
        return np.round(np.stack([o, h, lo, c], 1) * 10).astype(int).tolist()

    cen = b["centre"]
    last_min = mins[end - 1]
    ci = np.clip(last_min - b["centre_m0"], 0, len(cen) - 1)
    cv = cen[ci]
    return {"t": (g[idx] * tf * 60).astype(int).tolist(), "S": one(b["S"]), "B": one(b["B"]),
            "c": [None if not np.isfinite(x) else int(round(x * 10)) for x in cv]}


def dev_hist(h):
    """Гистограмма отклонения середины от центра: подбирает диапазон, чтобы хвосты были < 0,1 %, 80 корзин."""
    tot = h.sum()
    if tot <= 0:
        return None
    for rng in (10, 20, 40, 80, 150, 300):
        a, b = int((300 - rng) / 0.1), int((300 + rng) / 0.1)
        if (h[:a].sum() + h[b:].sum()) / tot < 0.001:
            break
    a, b = int((300 - rng) / 0.1), int((300 + rng) / 0.1)
    core_ = h[a:b]
    k = len(core_) // 80
    v = core_[: k * 80].reshape(80, k).sum(1)
    return {"lo": -rng, "step": 2 * rng / 80, "v": [round(float(x), 1) for x in v], "under": float(h[:a].sum()), "over": float(h[b:].sum())}


def pair_fee_stats(res, det):
    """По парам бирж при пороге комиссий: эпизодов на монету в час, время схождения p50/p90 (с), доля времени с запасом ≥ порога."""
    li = res["levels"].index(core.FEE_RT_BPS)
    hours = (res["t1"] - res["t0"]) / 3.6e6
    out = {}
    for r in (x for x in res["rows"] if "eps" in x):
        out.setdefault((r["A"], r["B"]), []).append(r)
    ret = []
    for (a, b), rs in sorted(out.items(), key=lambda kv: [list(NAMES).index(kv[0][0]), list(NAMES).index(kv[0][1])]):
        taus = np.array([e[2] for r in rs for e in det[(r["coin"], a, b)]["eps"][res["levels"][li]] if not e[3]], float)
        n = sum(r["eps"][li]["n"] for r in rs)
        w = np.array([r["cover_s"] for r in rs])
        ret.append({"a": a, "b": b, "per_h": n / (len(rs) * hours), "p50": float(np.median(taus) / 1000) if len(taus) else None,
                    "p90": float(np.percentile(taus, 90) / 1000) if len(taus) else None,
                    "share": float(np.average([r["share"][li] for r in rs], weights=w))})
    return ret


def main(res_p, det_p, v3_p, page_p, tmpl_p, out_p, res_rx_p=None, det_rx_p=None):
    res = json.load(open(res_p, encoding="utf-8"))
    det = pickle.load(open(det_p, "rb"))
    snap = json.load(open(v3_p, encoding="utf-8"))
    page = open(page_p, encoding="utf-8").read()
    tmpl = open(tmpl_p, encoding="utf-8").read()
    t0, t1 = res["t0"], res["t1"]
    levels = res["levels"]
    fee_i = levels.index(core.FEE_RT_BPS)
    info = res["info"]
    rows = [r for r in res["rows"] if "eps" in r]
    rows.sort(key=lambda r: (r["coin"], r["A"], r["B"]))
    # ---- строки ----
    out_rows, detail = [], {}
    pooled = {}   # (A, B, level) -> список эпизодов
    for i, r in enumerate(rows):
        key = (r["coin"], r["A"], r["B"])
        d = det[key]
        inf = info[r["coin"]]
        eps = []
        bins = []
        for s in r["eps"]:
            tau = s["tau_s"] or [None, None, None]
            eps.append([s["n"], s["conv"], s["cens"], r1(tau[0]) if tau[0] is None or tau[0] >= 10 else round(tau[0], 3), r1(tau[1]) if tau[1] is None or tau[1] >= 10 else round(tau[1], 3),
                        r1(tau[2]), r1(s["peak_med"]), r1(s["gross_med"]), s["gross_ge_fee"], s["gross_gt0"], None if s["size_med"] is None else round(s["size_med"])])
            bins.append(s["bins"])
        out_rows.append({
            "i": i, "c": r["coin"], "a": r["A"], "b": r["B"], "h": round(r["cover_s"] / 3600, 2), "n": r["n"],
            "ce": r1(r["centre"]), "w": r1(r["width_p90"]), "gap": [r1(x) for x in r["gap"]],
            "S": [r1(x) for x in r["Sq"]], "B": [r1(x) for x in r["Bq"]],
            "sh": [round(x, 5) for x in r["share"]], "eps": eps, "bins": bins,
            "cr": [round(r["cross_share"], 5), r["cross_n"]] + ([round(x) for x in r["cross_dur_ms"]] if r["cross_dur_ms"] else [None, None, None]),
            "mx": [r1(r["S_max"]), r1(r["B_min"])], "eq": [r1(x) for x in r["edge_q"]],
            "dep": [round(inf[r["A"]]["depth_usd"] or 0), round(inf[r["B"]]["depth_usd"] or 0)],
        })
        for li, lv in enumerate(levels):
            pooled.setdefault((r["A"], r["B"], li), []).extend(d["eps"][lv])
    # ---- пары бирж ----
    rows_by_key = {(r["coin"], r["A"], r["B"]): r["edge_x"] for r in rows}
    rows_by_key = {k: v for k, v in rows_by_key.items()}
    by_pair = {}
    for r in out_rows:
        by_pair.setdefault((r["a"], r["b"]), []).append(r)
    pairs = []
    for (a, b), rs in sorted(by_pair.items(), key=lambda kv: [list(NAMES).index(kv[0][0]), list(NAMES).index(kv[0][1])]):
        hrs = np.array([r["h"] for r in rs])
        entry = {"a": a, "b": b, "coins": len(rs), "centre": r1(np.median([abs(r["ce"]) for r in rs if r["ce"] is not None])),
                 "gap": r1(np.median([r["gap"][0] for r in rs])),
                 "share": [float(np.average([r["sh"][li] for r in rs], weights=hrs)) for li in range(len(levels))],
                 "ex": [float(np.average([rows_by_key[(r["c"], r["a"], r["b"])][k] for r in rs], weights=hrs)) for k in range(len(res["edge_x"]))], "eps": []}
        for li in range(len(levels)):
            ep = pooled.get((a, b, li), [])
            conv = np.array([e[2] for e in ep if not e[3]], float)
            gr = [e[5] for e in ep if e[5] is not None]
            n = len(ep)
            entry["eps"].append({
                "n": n, "conv": int(len(conv)), "cens": n - len(conv),
                "per_coin_h": n / (len(rs) * float(np.mean(hrs)) or 1),
                "tau": [float(np.median(conv) / 1000), float(np.percentile(conv, 90) / 1000)] if len(conv) else None,
                "bins": np.bincount(np.searchsorted(core.TAU_EDGES_MS, conv, "right"), minlength=len(core.TAU_EDGES_MS) + 1).tolist() if len(conv) else [0] * (len(core.TAU_EDGES_MS) + 1),
                "ge_fee": int(sum(1 for g in gr if g >= core.FEE_RT_BPS)),
                "size": float(np.median([e[6] for e in ep])) if ep else None})
        pairs.append(entry)
    # ---- KPI ----
    coins_n = {}
    for c, i in info.items():
        coins_n[c] = sum(1 for e in i.values() if e)
    by_n = {k: sum(1 for v in coins_n.values() if v == k) for k in (4, 3, 2)}
    allfee = [e for k, v in pooled.items() if k[2] == fee_i for e in v]
    conv = np.array([e[2] for e in allfee if not e[3]], float)
    hrs_all = np.array([r["h"] for r in out_rows])
    kpi = {"coins": sum(1 for v in coins_n.values() if v >= 2), "by_n": by_n, "rows": len(out_rows), "mismatch": len(res["mismatch"]),
           "share_fee": float(np.average([r["sh"][fee_i] for r in out_rows], weights=hrs_all)),
           "tau": {"p50": float(np.median(conv) / 1000) if len(conv) else None, "p90": float(np.percentile(conv, 90) / 1000) if len(conv) else None,
                   "n": len(allfee), "cens": len(allfee) - len(conv)}}
    # ---- детали (бары 5 и 15 мин у всех, 1 мин — у лидеров по числу эпизодов при пороге комиссий) ----
    ranked = sorted(out_rows, key=lambda r: -r["eps"][fee_i][0])
    top = {r["i"] for r in ranked[:TOP_1MIN]}
    sel = {r["i"] for r in ranked[:60]} | {r["i"] for r in out_rows if r["c"] in ("BTC", "ETH", "SOL", "XRP", "DOGE")}
    for pr in pairs:
        sel |= {r["i"] for r in [x for x in ranked if (x["a"], x["b"]) == (pr["a"], pr["b"])][:8]}
    for r in out_rows:
        if r["i"] not in sel:
            continue
        d = det[(r["c"], r["a"], r["b"])]
        e = {"tf5": bars_tf(d["bars"], 5), "tf15": bars_tf(d["bars"], 15), "dev": dev_hist(d["hist"]["hDev"]), "eps": []}
        if r["i"] in top:
            e["tf1"] = bars_tf(d["bars"], 1)
        for lv in levels:
            lst = sorted(d["eps"][lv], key=lambda x: -x[4])[:8]
            e["eps"].append([[int(x[0] / 1000), x[1], -1 if x[3] else x[2], r1(x[4]), r1(x[5])] for x in lst])
        if e["dev"] is None:
            e["dev"] = {"lo": -10, "step": .25, "v": [0] * 80, "under": 0, "over": 0}
        detail[r["i"]] = e
    rxcmp = None
    if res_rx_p:
        res_rx, det_rx = json.load(open(res_rx_p, encoding="utf-8")), pickle.load(open(det_rx_p, "rb"))
        e_s, rx_s = pair_fee_stats(res, det), pair_fee_stats(res_rx, det_rx)
        rxcmp = [{"a": x["a"], "b": x["b"], "e": x, "rx": y} for x, y in zip(e_s, rx_s)]
        del det_rx
    from common import load_pool
    day = __import__("datetime").datetime.fromtimestamp(t0 / 1000, __import__("datetime").timezone.utc).strftime("%Y-%m-%d")
    excl = {ex: [s.replace("USDT", "") for s in load_pool(ex, day)[2].get("top_excluded") or []] for ex in ("binance", "bybit")}
    minqv = load_pool("binance", day)[2]["min_quote_volume"]
    arb = {"excl": excl, "minqv": minqv, "rxcmp": rxcmp, "t0": t0, "t1": t1, "hours": (t1 - t0) / 3.6e6, "fee": core.FEE_RT_BPS, "levels": levels, "edge_x": res["edge_x"], "names": NAMES, "lag": res["lag"],
           "kpi": kpi, "pairs": pairs, "rows": out_rows, "detail": {str(k): v for k, v in detail.items()}}
    # ---- страница здоровья как вкладка 1 ----
    css = re.search(r"<style>(.*?)</style>", page, re.S).group(1)
    js = re.search(r"<script>(.*?)</script>", page, re.S).group(1)
    js = re.sub(r"async function load\(\)\{.*?\n\}\n", "function load(){D=SNAP;render()}\n", js, flags=re.S)
    js = js.replace("load();setInterval(load,30000);", "load();")
    assert "SNAP" in js and "setInterval" not in js
    js = "const SNAP=" + json.dumps(snap, ensure_ascii=False, separators=(",", ":")) + ";\n" + js
    html = tmpl.replace("/*HEALTH_CSS*/", css).replace("/*HEALTH_JS*/", js).replace("__ARB__", json.dumps(arb, ensure_ascii=False, separators=(",", ":")))
    open(out_p, "w", encoding="utf-8").write(html)
    print(json.dumps({"bytes": len(html), "rows": len(out_rows), "pairs": len(pairs), "kpi": kpi}, ensure_ascii=False)[:600])


if __name__ == "__main__":
    main(*sys.argv[1:9])
