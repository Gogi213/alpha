#!/usr/bin/env python3
"""TK-622: results.csv (`tk622_killzones.py`) → tables.md (по схемам и сторонам) и stable.md (устойчивые номинальные корзины)."""
import json
import sys

import pandas as pd

R = pd.read_csv(sys.argv[1] + "/results.csv")
OUT = sys.argv[1]
MET = [("touch", "касание, %", 100, "%.0f"), ("brk", "≥1 NATR, %", 100, "%.0f"), ("mo1", "мо 1м, бп", 1, "%+.0f"), ("mo5", "мо 5м, бп", 1, "%+.0f"),
       ("mo15", "мо 15м, бп", 1, "%+.0f"), ("mo60", "мо 60м, бп", 1, "%+.0f")]
SIDE = {"L": "лонг (L_sig / L_base)", "S": "шорт (S_sig / S_base)"}


def mark(r):
    if r is None or "p" not in r or r["p"] != r["p"]:
        return ""
    if r["p"] < 0.05:
        return "**" if r["stable"] else "*"
    return ""


def key(df):
    return {(a, b, c): r for a, b, c, r in zip(df["side"], df["scheme"], df["bucket"], df.to_dict("records"))}


def lookup(side, scheme, bucket, met):
    d = R[(R["side"] == side) & (R["scheme"] == scheme) & (R["bucket"] == bucket) & (R["metric"] == met)]
    return d.iloc[0].to_dict() if len(d) else None


def fmt(r, sc, f, plain=False):
    if r is None or r.get("mean_in") != r.get("mean_in"):
        return "—"
    return f % (r["mean_in"] * sc) + ("" if plain else mark(r))


md = ["# Килзоны пробоев П-42: таблицы по схемам (TK-622)", "",
      "Время корзины — время сигнала, UTC. Значения — среднее в корзине; `*` — номинальное p < 0,05 (тест со сдвигом часов), `**` — то же и знак эффекта «в корзине − вне корзины» совпал в ≥ 4 из ≥ 4 учтённых месяцев. После Холма (444 теста) значимых нет. Сделки базы: n / Σ$ / $ на сделку (без медиан). Строка «вся выборка» — среднее по всем сигналам стороны.", ""]
for side in "LS":
    sub = R[R["side"] == side]
    md.append("## " + SIDE[side])
    allr = {}
    for m, *_ in MET + [("gf", "", 1, "")]:
        d = sub[sub["metric"] == m].iloc[0]
        allr[m] = (d["mean_in"] * d["n_in"] + d["mean_out"] * d["n_out"]) / (d["n_in"] + d["n_out"]), int(d["n_in"] + d["n_out"])
    for scheme in dict.fromkeys(sub["scheme"]):
        md += ["", "### " + scheme, "", "| корзина | n сигн. | " + " | ".join(m[1] for m in MET) + " | база n / Σ$ / $/сд. |", "|" + "---|" * (len(MET) + 3)]
        if scheme == "час (мин до/после начала часа)":
            row = ["вся выборка", str(allr["touch"][1])] + [("%s" % m[3]) % (allr[m[0]][0] * m[2]) for m in MET]
            row.append("%d / %+.1f / %+.2f" % (allr["gf"][1], allr["gf"][0] * allr["gf"][1], allr["gf"][0]))
            md.append("| " + " | ".join(row) + " |")
        for bucket in dict.fromkeys(sub[sub["scheme"] == scheme]["bucket"]):
            t = lookup(side, scheme, bucket, "touch")
            row = [bucket, str(int(t["n_in"]))] + [fmt(lookup(side, scheme, bucket, m[0]), m[2], m[3]) for m in MET]
            g = lookup(side, scheme, bucket, "gf")
            row.append("%d / %+.1f / %+.2f%s" % (g["n_in"], g["sum_in"], g["mean_in"], mark(g)) if g and g["sum_in"] == g["sum_in"] else "%d / —" % g["n_in"] if g else "—")
            md.append("| " + " | ".join(row) + " |")
    md.append("")
open(OUT + "/tables.md", "w", encoding="utf-8").write("\n".join(md) + "\n")

# устойчивые номинальные корзины
P = R[R["primary"] & R["p"].notna() & (R["p"] < 0.05) & R["stable"]].sort_values("p")
st = ["| сторона | схема | корзина | метрика | n | в корзине | вне | эффект [95 %] | p | p Холм | месяцы июл…ноя | без 2 суток | без 2 монет |", "|" + "---|" * 13]
for r in P.to_dict("records"):
    sc = 100 if r["metric"] in ("touch", "brk") else 1
    unit = {"touch": "пп", "brk": "пп", "mo60": "бп", "gf": "$"}[r["metric"]]
    ms = " / ".join("—" if x is None else ("%+.2f" % x if r["metric"] == "gf" else "%+.0f" % (x * sc)) for x in json.loads(r["months"]))
    d2 = lambda x: "—" if x != x else ("%+.2f" % x if r["metric"] == "gf" else "%+.0f" % (x * sc))
    st.append("| %s | %s | %s | %s | %d | %s | %s | %s %s [%s; %s] | %.4f | %.2f | %s | %s | %s |" % (
        r["side"], r["scheme"], r["bucket"], r["metric"], r["n_in"],
        ("%+.2f" if r["metric"] == "gf" else "%.1f") % (r["mean_in"] * sc), ("%+.2f" if r["metric"] == "gf" else "%.1f") % (r["mean_out"] * sc),
        ("%+.2f" if r["metric"] == "gf" else "%+.1f") % (r["D"] * sc), unit, ("%.2f" if r["metric"] == "gf" else "%.1f") % (r["lo"] * sc),
        ("%.2f" if r["metric"] == "gf" else "%.1f") % (r["hi"] * sc), r["p"], r["p_holm"], ms, d2(r["D_drop2days"]), d2(r["D_drop2coins"])))
open(OUT + "/stable.md", "w", encoding="utf-8").write("\n".join(st) + "\n")
print(len(P), "stable nominal rows")
