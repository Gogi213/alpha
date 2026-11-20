#!/usr/bin/env python3
"""TK-660 (§13.60): строки таблицы отчёта из res/cells60-S.json + res/verdict60.json. python3 tk660_report.py <каталог res>"""
import json
import os
import sys

d = sys.argv[1]
c = json.load(open(os.path.join(d, "cells60-S.json"), encoding="utf-8"))
v = json.load(open(os.path.join(d, "verdict60.json"), encoding="utf-8"))
f = lambda x, n=2: ("%+.*f" % (n, x)).replace(".", ",")      # noqa: E731
b = c["base"]
print("base n %d Σ %s mean %s Sharpe %.2f R:R %.2f stops %d mean %s max %s (%.2f %% / %.2f %%)" % (
    b["n"], f(b["sum"]), f(b["mean"], 3), b["sharpe"], b["rr"]["rr"], b["rr"]["stops"]["n"], f(b["rr"]["stops"]["mean_loss"]),
    f(b["rr"]["stops"]["max_loss"]), b["rr"]["stops"]["mean_loss_pct"], b["rr"]["stops"]["max_loss_pct"]))
for cid, r in c["cells"].items():
    dl, vv = r["delta"], v["cells"][cid]
    st = r["rr"]["stops"]
    print("| E(%d) | %d | %s | %s | %.2f | %.2f | %d / %s / %s | %s [%s] | %.3f | %s (%s) | %s | %s | %s | %s | %s |" % (
        r["ax"]["k"], r["n"], f(r["sum"], 1), f(r["mean"], 3), r["sharpe"], r["rr"]["rr"], st["n"], f(st["mean_loss"], 1),
        f(st["max_loss"], 1), f(dl["d"], 3), f(dl["lower"], 3), dl["p"], f(dl["dsum"], 1), ("%.1f" % dl["se_sum"]).replace(".", ","),
        " / ".join("—" if x is None else f(x) for x in r["dmonths"]),
        "/".join(str(m[0]) for m in r["months"]),
        "%(filled)d / %(expired)d / %(touched)d / %(rejected)d / %(not_placed)d" % r["ek"],
        "%d (%s); новых %d (%s)" % (r["skipped_base_n"], f(r["skipped_base_sum"], 1), r["same"]["only_cell"], f(r["new_cell_sum"], 1)),
        ", ".join(k for k, ok in vv["gates"].items() if ok) + ("; по отбору" if vv["by_selection"] else "")))
    nz = vv["noise"]
    print("   noise bar %.2f deflated %s survives %s; nb %s; robust d %s c %s" % (nz["noise_bar"], nz.get("deflated_t", nz.get("dsr")), nz["survives"], vv["neighbours"], r["robust_days"], r["robust_coins"]))
print("answer:", v["answer"], "| G-dup", c["gdup"], "| forced", c["gwin_forced"], c["gwin_forced_base"], "| tmax", c["tape_max_exit_ms"])
