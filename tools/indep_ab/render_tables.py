#!/usr/bin/env python3
"""Markdown tables from analyze.py JSON output (an-bingx-*.json, an-binance-*.json). Usage: render_tables.py <bingx.json> <binance.json>"""
import json, sys


def f(x, nd=1):
    return "—" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def prof_table(pi, pp, hour):
    rows = []
    ind, prod = pi[hour], (pp or {}).get(hour, [])
    for i, r in enumerate(ind):
        p = prod[i] if i < len(prod) else {}
        rows.append([r["win"], r["n"], f(r.get("lag_p50")), f(r.get("lag_p99")), f(r.get("lag_max")), f(r.get("uk_max"), 0),
                     p.get("n", "—"), f(p.get("lag_p50")), f(p.get("lag_p99")), f(p.get("lag_max"))])
    return table(["окно, с от часа", "незав. n", "p50", "p99", "max", "u−k max", "бой n", "p50", "p99", "max"], rows)


def main():
    bx = json.load(open(sys.argv[1]))
    print("## BingX window", bx["window_utc"], "| ind rows", bx["ind_rows"], "| prod rows", bx["prod_rows"])
    print("\n### lag by connection (ms)\n")
    rows = []
    for c, v in bx["lag_by_conn"].items():
        uk = v.get("uk", {})
        rows.append([c, v["n"], f(v["p1"]), f(v["p5"]), f(v["p25"]), f(v["p50"]), f(v["p75"]), f(v["p95"]), f(v["p99"]), f(v["p99.9"]),
                     f(uk.get("p50"), 2), f(uk.get("p99"), 1), f(uk.get("p100"), 0)])
    print(table(["conn", "n", "p1", "p5", "p25", "p50", "p75", "p95", "p99", "p99.9", "u−k p50", "u−k p99", "u−k max"], rows))
    print("\n### ws ping RTT (client, ms)\n")
    print(table(["conn", "peer", "pings", "p5", "p50", "p95", "p99", "closes"],
                [[c, ",".join(v["peer"]), v["pings"], f(v["p5"]), f(v["p50"]), f(v["p95"]), f(v["p99"]), v["closes"]] for c, v in bx["ws_ping_rtt_ms"].items()]))
    print("\n### ind − prod per trade id (ms), each connection\n")
    print(table(["conn", "matched", "of ind", "neg share", "mean", "p5", "p25", "p50", "p75", "p95", "p99"],
                [[c, v["matched"], v["of_ind"], f(v["neg_share"], 3), f(v["mean"]), f(v["p5"]), f(v["p25"]), f(v["p50"]), f(v["p75"]), f(v["p95"]), f(v["p99"])]
                 for c, v in bx["diff_ind_minus_prod_ms"].items()]))
    print("\n### coverage and first-of-4 vs prod\n")
    rows = []
    for s, v in bx["coverage"].items():
        d = v["first_of_4_minus_prod_ms"]
        rows.append([s, v["ind_union"], v["prod"], v["both"], v["only_ind"], v["only_prod"], d["n"], f(d["neg_share"], 3), f(d["p5"]), f(d["p25"]), f(d["p50"], 2), f(d["p75"], 2), f(d["p95"]), f(d["p99"])])
    print(table(["set", "ind union", "prod", "both", "only ind", "only prod", "n", "neg share", "p5", "p25", "p50", "p75", "p95", "p99"], rows))
    print("\n### pairs of independent connections\n")
    rows = []
    for s, v in bx["coverage"].items():
        for p, x in v["pairs"].items():
            rows.append([s, p.replace("|", "+"), x["n"], f(x["i_minus_j_p5"]), f(x["i_minus_j_p50"], 2), f(x["i_minus_j_p95"]), f(x["min_pair_minus_prod_p50"], 2)])
    print(table(["set", "pair", "n", "i−j p5", "i−j p50", "i−j p95", "min(pair)−prod p50"], rows))
    print("\n### lag p50 by 10 min\n")
    tt = bx["lag_p50_by_10min"]
    cols = sorted({c for v in tt.values() for c in v})
    print(table(["UTC"] + cols, [[t] + [f(v.get(c)) for c in cols] for t, v in tt.items()]))
    print("\n### baseline outside :00 (first 2 min of hour excluded)\n")
    print(json.dumps({"ind": bx["ind_baseline_outside_00"], "prod": bx["prod_baseline_outside_00"]}, ensure_ascii=False))
    for h in bx["profile_ind"]:
        print(f"\n### BingX profile around {h} (lag ms, kernel stamps; ind = 8 connections pooled)\n")
        print(prof_table(bx["profile_ind"], bx["profile_prod"], h))
    print("\n### BingX arrival gaps\n")
    for k in ("arrival_gaps_ind_pooled", "arrival_gaps_prod_pooled"):
        g = bx[k]
        print(k, "by_hour:", json.dumps(g["by_hour"], ensure_ascii=False))
        print(k, "overall_top:", json.dumps(g["overall_top"], ensure_ascii=False))
    print("\nu−k max seconds:", json.dumps(bx["uk_max_seconds"]))
    if len(sys.argv) > 2:
        bn = json.load(open(sys.argv[2]))
        print("\n## Binance window", bn["window_utc"], "| ind rows", bn["ind_rows"], "| prod rows", bn["prod_rows"], "| symbols", bn["symbols"])
        for k, name in (("b", "bookTicker"), ("t", "aggTrade")):
            d = bn[f"diff_ind_minus_prod_ms_{k}"]
            print(f"\n### {name}: ind − prod per id (ms)\n")
            print(table(["matched", "of ind", "neg share", "p1", "p5", "p25", "p50", "p75", "p95", "p99", "p99.9"],
                        [[d["matched"], d["of_ind"], f(d["neg_share"], 3)] + [f(d[q], 2) for q in ("p1", "p5", "p25", "p50", "p75", "p95", "p99", "p99.9")]]))
            lg = bn[f"lag_{k}"]
            print(table(["side", "p1", "p5", "p25", "p50", "p75", "p95", "p99", "p99.9"],
                        [[s] + [f(lg[s][q]) for q in ("p1", "p5", "p25", "p50", "p75", "p95", "p99", "p99.9")] for s in ("ind", "prod")]))
            print("u−k:", json.dumps(lg["ind_uk"]))
            for h in bn[f"profile_ind_{k}"]:
                print(f"\n#### Binance {name} around {h}\n")
                print(prof_table(bn[f"profile_ind_{k}"], bn[f"profile_prod_{k}"], h))
        print("\n### Binance ind rate around hour\n", json.dumps(bn["ind_rate_around_hour"], ensure_ascii=False))
        print("\narrival_gaps by_hour:", json.dumps(bn["arrival_gaps_ind_pooled"]["by_hour"], ensure_ascii=False))
        print("u−k max seconds:", json.dumps(bn["uk_max_seconds"]))


if __name__ == "__main__":
    main()
