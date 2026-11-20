#!/usr/bin/env python3
"""П-42 §13.48 (TK-648): отчёт из res/select48.json и res/counts48-*.json → markdown (stdout).
  python3 tk648_report.py <каталог res>"""
import json
import math
import os
import sys

RES = sys.argv[1]
SIDE = {"L": "Лонг", "S": "Шорт"}
FEAT = {"IMB02": "дисбаланс ±0,2 %", "IMB1": "дисбаланс ±1 %", "IMB2": "дисбаланс ±2 %", "THIN5": "стена впереди за 5 мин", "THIN30": "стена впереди за 30 мин", "DTURN": "глубина впереди / оборот 1 ч"}


def f(x, d=1, sign=True):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    s = ("%.*f" % (d, abs(x))).replace(".", ",")
    z = float(s.replace(",", ".")) == 0
    return ("−" if x < 0 and not z else ("+" if sign and not z else "")) + s


def what(c):
    short, rest = c["cid"].split("_")
    tail, pct = rest[:2], int(rest[2:])
    return "%s: убрать %s %d %%" % (FEAT[short], "нижние" if tail == "lo" else "верхние", pct)


sel = json.load(open(os.path.join(RES, "select48.json"), encoding="utf-8"))
bar72 = sel["noise"]["72"]
out = []
for s in ("L", "S"):
    r = sel["sides"][s]
    b = r["base"]
    st = b["rr"]["stops"]
    out.append("### %s: база на ω — %d сделок, Σ %s $, %s $/сделку, Sharpe %s; R:R %s, стопов %d, убыток стопа %s $ = %s %% маржи при плече 2" % (
        SIDE[s], b["n"], f(b["sum"]), f(b["mean"], 2), f(b["sharpe"], 2, False), f(b["rr"]["rr"], 2, False), st["n"], f(st["mean_loss"], 2), f(st["mean_loss_pct"], 1, False)))
    out.append("")
    out.append("По месяцам ω (сделок / Σ $): " + "; ".join("%s %d / %s" % (k, v[0], f(v[1])) for k, v in b["months"].items()) + ". Ответ по стороне: **%s**." % r["answer"])
    out.append("")
    out.append("| клетка | сделок | Σ, $ | $/сделку | Sharpe | ΔΣ к базе, $ [95 %] | p | Холм 6 / 73 | MDE, $ | ΔΣ сен / окт / ноя | вывод п. 7 |")
    out.append("|---|---:|---:|---:|---:|---|---:|---|---:|---|---|")
    for cid, c in r["cells"].items():
        if not c["trial"]:
            continue
        d, x = c["delta"], c["stats"]
        out.append("| %s (`%s`) | %d | %s | %s | %s | %s [%s; %s] | %s | %s / %s | %.0f | %s | %s |" % (
            what(c), cid, x["n"], f(x["sum"]), f(x["mean"], 2), f(x["sharpe"], 2, False), f(d["dsum"]), f(d["lo95"]), f(d["hi95"]), f(d["p2"], 3, False),
            "да" if c["holm_reject"] else "нет", "да" if c["holm73_reject"] else "нет", d["mde_sum_holm"], " / ".join(f(v) for v in d["months"].values()),
            c["verdict"].replace("|", "\\|")))
    out.append("")
    out.append("Все 36 клеток стороны (пробные, соседи, обратный хвост — описательно):")
    out.append("")
    out.append("| клетка | роль | сделок | Σ, $ | Sharpe | ΔΣ, $ | z | шум Sharpe (планка 72 / дефл.) |")
    out.append("|---|---|---:|---:|---:|---:|---:|---|")
    for cid, c in r["cells"].items():
        d, x = c["delta"], c["stats"]
        ns = c.get("noise_sharpe72") or {}
        role = "пробная" if c["trial"] else ("сосед" if c["mech"] else "обратный")
        out.append("| %s | %s%s | %d%s | %s | %s | %s | %s | %s / %s |" % (cid, role, "" if c["admitted"] else ", не доп.", x["n"], " мало" if x["few"] else "", f(x["sum"]), f(x["sharpe"], 2, False),
                                                       f(d["dsum"]), f(d["z"], 2), f(ns.get("noise_bar"), 2, False), f(ns.get("deflated"), 2)))
    zs = [abs(c["delta"]["z"]) for c in r["cells"].values()]
    pos = sum(1 for c in r["cells"].values() if c["delta"]["dsum"] > 0)
    out.append("")
    out.append("ΔΣ > 0 у %d из 36 клеток; максимум |z| %s при ожидаемом максимуме шума из 72 клеток %s (95-й перцентиль %s)." % (
        pos, f(max(zs), 2, False), f(bar72["emax_abs_z"], 2, False), f(bar72["p95_max_abs_z"], 2, False)))
    out.append("")
print("\n".join(out))
