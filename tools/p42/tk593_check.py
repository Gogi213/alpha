"""П-42 TK-593: сверка страницы с источниками. Источники (tree_*.csv, pairs/summary.json) → данные внутри index.html → метки и таблицы в DOM (dom_<side>.json из tk593_shots.js).

Использование: <venv>/bin/python tk593_check.py <tree TK-592> <pairs TK-592> <index.html> <каталог с dom_*.json> [ep_u0.parquet <каталог кода TK-592>]
Если даны таблица и каталог кода — дополнительно пересчитываются AUC «в выборке» и на проверке из .cbm независимым проходом.
TK-601: ещё проверяет блок «Ход работ» (data/progress.json рядом с index.html → вшито → dom_progress.json).
Выход: число проверок и расхождений; код 1 при любом расхождении.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys

import numpy as np
import pandas as pd

tree_dir, pairs_dir, html_path, dom_dir = sys.argv[1:5]
table, code_dir = (sys.argv[5], sys.argv[6]) if len(sys.argv) > 6 else (None, None)
DIRS = ("A->B", "B->A")
n_ok = 0
bad = []


def eq(what, a, b, tol=1e-9):
    global n_ok
    if a is None or b is None:
        ok = a is None and b is None
    elif isinstance(a, str) or isinstance(b, str):
        ok = re.sub(r"\s", "", str(a)) == re.sub(r"\s", "", str(b))
    else:
        ok = abs(float(a) - float(b)) <= tol * max(1.0, abs(float(b)))
    if ok:
        n_ok += 1
    else:
        bad.append((what, a, b))


def ys_eq(what, got, want):
    g = [float(v) for v in got.split(",")] if got else None
    eq(what + " len", len(g) if g else None, len(want))
    eq(what, max(abs(a - b) for a, b in zip(g, want)) if g else None, 0.0, 1e-9)


def num(v, d=3, sign=False):
    if v is None:
        return "—"
    s = f"{abs(v):.{d}f}".replace(".", ",")
    z = float(f"{abs(v):.{d}f}") == 0
    return ("−" if v < 0 and not z else ("+" if sign and not z else "")) + s


def ints(n):
    return str(int(n))


html = open(html_path, encoding="utf-8").read()
blob = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1).replace("<\\/", "</")
P = json.loads(blob)
auc = pd.read_csv(f"{tree_dir}/tree_auc.csv")
delta = pd.read_csv(f"{tree_dir}/tree_delta.csv")
perm = pd.read_csv(f"{tree_dir}/tree_perm.csv")
shap = pd.read_csv(f"{tree_dir}/tree_shap.csv")
gate = pd.read_csv(f"{tree_dir}/tree_gate_u1.csv")
psum = json.load(open(f"{pairs_dir}/summary.json"))

# ── 1. данные страницы против источников
assert P["meta"]["day_last"] < 74
for side in ("long", "short"):
    S = P["sides"][side]
    for dk in DIRS:
        c = S["dirs"][dk]
        w = f"{side} {dk}"
        a = auc[(auc.side == side) & (auc.dir == dk)]
        for m in ("CB3", "CB1", "LOGIT"):
            r = a[(a.set == "f12") & (a.model == m)].iloc[0]
            eq(f"{w} auc {m}", c["auc"][m], r.auc)
            eq(f"{w} roc.auc {m}", c["roc"][m]["auc"], r.auc)
            eq(f"{w} overfit val {m}", c["overfit"]["models"][m]["val"], r.auc)
        tr = a[a.model == "TURN"].iloc[0]
        eq(f"{w} auc TURN", c["auc"]["TURN"], tr.auc)
        eq(f"{w} roc.auc TURN", c["roc"]["TURN"]["auc"], tr.auc)
        eq(f"{w} n_T", c["n_T"], tr.n_T)
        eq(f"{w} k_T", c["k_T"], tr.k_T)
        cb3 = a[(a.set == "f12") & (a.model == "CB3")].iloc[0]
        ex = json.loads(cb3.extra)
        eq(f"{w} iters", c["lc"]["stop"], ex["iters"])
        eq(f"{w} lc len", len(c["lc"]["train"]), 3 * ex["iters"])
        eq(f"{w} lc len val", len(c["lc"]["val"]), 3 * ex["iters"])
        eq(f"{w} folds iters", str([f["iters"] for f in c["overfit"]["folds"]]), str(ex["cv_iters"]))
        eq(f"{w} folds n", sum(f["n"] for f in c["overfit"]["folds"]), c["n_S"])
        eq(f"{w} folds k", sum(f["k"] for f in c["overfit"]["folds"]), c["k_S"])
        eq(f"{w} n_folds", len(c["overfit"]["folds"]), 5)
        d = delta[(delta.side == side) & (delta.dir == dk) & (delta.set == "f12") & (delta.pair == "CB3-LOGIT")].iloc[0]
        for k in ("delta", "lo", "hi"):
            eq(f"{w} delta.{k}", c["delta"][k], d[k])
        eq(f"{w} delta=cb3-logit", c["delta"]["delta"], cb3.auc - a[(a.set == "f12") & (a.model == "LOGIT")].iloc[0].auc)
        pr = perm[(perm.side == side) & (perm.dir == dk) & (perm.set == "f12")]
        eq(f"{w} perm n", len(c["perm"]), len(pr))
        for r in c["perm"]:
            eq(f"{w} perm {r['feature']}", r["auc_drop"], pr[pr.feature == r["feature"]].auc_drop.iloc[0])
        sh = shap[(shap.side == side) & (shap.dir == dk) & (shap.set == "f12")].sort_values("mean_abs", ascending=False).head(10)
        eq(f"{w} shap n", len(c["shap"]), 10)
        for r, (_, s) in zip(c["shap"], sh.iterrows()):
            eq(f"{w} shap {s.f1}|{s.f2}", r["mean_abs"], s.mean_abs)
            eq(f"{w} shap names", f"{r['f1']}|{r['f2']}", f"{s.f1}|{s.f2}")
        g = gate[(gate.side == side) & (gate.dir == dk)].iloc[0]
        u = c["u1r"]
        eq(f"{w} u1 n", u["n"], g.n_u1)
        eq(f"{w} u1 k", u["k"], g.k_u1)
        eq(f"{w} u1 CB3", u["CB3"], g.auc_cb3_u1)
        eq(f"{w} u1 LOGIT", u["LOGIT"], g.auc_logit_u1)
        # кривые: внутренняя согласованность
        for m in ("CB3", "CB1", "LOGIT", "TURN"):
            t = np.array(c["roc"][m]["tpr"])
            eq(f"{w} roc trapz {m}", float(np.trapezoid(t, np.array(P["roc_fpr"]))), c["roc"][m]["auc"], 2e-3)
        dc = c["deciles"]
        eq(f"{w} deciles n", sum(x["n"] for x in dc), c["n_T"])
        eq(f"{w} deciles k", sum(x["k"] for x in dc), c["k_T"])
        eq(f"{w} base_y", c["base_y"], sum(x["k"] for x in dc) / sum(x["n"] for x in dc))
        eq(f"{w} calib mean pred vs base", float(np.average([x["pred"] for x in dc], weights=[x["n"] for x in dc])) > 0, True)
        st = c["strata"]
        eq(f"{w} strata day n", sum(x["n"] for x in st["day"]), c["n_T"])
        eq(f"{w} strata day k", sum(x["k"] for x in st["day"]), c["k_T"])
        for kind in ("lturn24", "natr_bp", "age_h"):
            eq(f"{w} strata {kind} n", sum(x["n"] for x in st[kind]), c["n_T"])
            eq(f"{w} strata {kind} k", sum(x["k"] for x in st[kind]), c["k_T"])
        for kind, items in st.items():
            for x in items:
                if x["auc_cb3"] is not None and x["auc_logit"] is not None:
                    eq(f"{w} strata {kind} delta", x["delta"], x["auc_cb3"] - x["auc_logit"])
                eq(f"{w} strata {kind} small", x["small"], x["n"] < 300)
    # вердикт
    d = [S["dirs"][k]["delta"] for k in DIRS]
    eq(f"{side} verdict a", S["verdict"]["a"]["ok"], all(x["delta"] >= 0.02 and x["lo"] > 0 for x in d))
    conf = [psum[f"{side}_{k[0]}to{k[-1]}"]["confirmed"] for k in DIRS]
    eq(f"{side} confirmed", str(S["verdict"]["b"]["confirmed"]), str(conf))
    eq(f"{side} verdict b", S["verdict"]["b"]["ok"], min(conf) >= 1)

# ── 2. независимый пересчёт «в выборке» из .cbm
if table:
    sys.path.insert(0, code_dir)
    import tk592_tree as T
    from catboost import CatBoostClassifier
    from tk592_lib import FEATS
    df = T.add_cols(T.prepare(pd.read_parquet(table)))
    eq("table: день ≤ 73", int(df["day"].max()) <= 73, True)
    for side in ("long", "short"):
        D = df[df.side == side]
        for sh, th, dk in (("A", "B", "A->B"), ("B", "A", "B->A")):
            S, Tt = D[D.half == sh], D[D.half == th]
            m = CatBoostClassifier()
            m.load_model(f"{tree_dir}/models/cb3_{side}_{sh}_f12.cbm")
            c = P["sides"][side]["dirs"][dk]
            eq(f"{side} {dk} cbm in-sample", T.auc(m.predict_proba(S[FEATS].to_numpy())[:, 1], S["y"].to_numpy()), c["overfit"]["models"]["CB3"]["train"], 1e-9)
            eq(f"{side} {dk} cbm val", T.auc(m.predict_proba(Tt[FEATS].to_numpy())[:, 1], Tt["y"].to_numpy()), c["overfit"]["models"]["CB3"]["val"], 1e-9)
            eq(f"{side} {dk} n_S", len(S), c["n_S"])

# ── 3. DOM против данных страницы
for side in ("long", "short"):
    dom = json.load(open(f"{dom_dir}/dom_{side}.json"))
    M, TB = dom["marks"], dom["tables"]
    S = P["sides"][side]
    for dk in DIRS:
        c = S["dirs"][dk]
        w = f"DOM {side} {dk}"
        mk = lambda name: [e for e in M.get(f"{dk}|{name}", []) if e]  # у самого <svg> графика нет data-атрибутов, кроме data-chart
        for m in ("CB3", "CB1", "LOGIT", "TURN"):
            e = mk(f"roc.{m}")
            eq(f"{w} roc.{m} есть", len(e), 1)
            if e:
                ys_eq(f"{w} roc.{m} ys", e[0]["ys"], c["roc"][m]["tpr"])
        eq(f"{w} decile.y n", len(mk("decile.y")), 10)
        for e in mk("decile.y"):
            d = c["deciles"][int(e["i"])]
            eq(f"{w} decile y", float(e["y"]), d["y"], 1e-6)
            eq(f"{w} decile n", e["n"], d["n"])
            eq(f"{w} decile k", e["k"], d["k"])
        for e in mk("decile.c"):
            eq(f"{w} decile c", float(e["y"]), c["deciles"][int(e["i"])]["c"], 1e-6)
        for e in mk("decile.cut"):
            eq(f"{w} cut", float(e["x"]), c["tertile_cut_q"][int(e["i"])] * 10, 1e-6)
        eq(f"{w} calib n", len(mk("calib")), 10)
        for e in mk("calib"):
            d = c["deciles"][int(e["i"])]
            eq(f"{w} calib x", float(e["x"]), d["pred"], 1e-6)
            eq(f"{w} calib y", float(e["y"]), d["y"], 1e-6)
        pe = sorted(c["perm"], key=lambda r: -r["auc_drop"])
        eq(f"{w} perm n", len(mk("perm")), 12)
        for e in mk("perm"):
            eq(f"{w} perm {e['label']}", float(e["y"]), pe[int(e["i"])]["auc_drop"], 1e-6)
            eq(f"{w} perm label", e["label"], pe[int(e["i"])]["feature"])
        eq(f"{w} shap n", len(mk("shap")), 10)
        for e in mk("shap"):
            r = c["shap"][int(e["i"])]
            eq(f"{w} shap val", float(e["y"]), r["mean_abs"] * 1e3, 1e-6)
            eq(f"{w} shap label", e["label"], f"{r['f1']}|{r['f2']}")
        for k in ("train", "val"):
            e = mk(f"lc.{k}")
            ys_eq(f"{w} lc.{k}", e[0]["ys"] if e else None, c["lc"][k])
        for e in mk("lc.stop"):
            eq(f"{w} lc.stop", float(e["x"]), c["lc"]["stop"])
        ov = c["overfit"]
        want = [ov["models"]["CB3"]["train"]] + [f["auc"] for f in ov["folds"]] + [ov["models"]["CB3"]["val"]]
        eq(f"{w} overfit n", len(mk("overfit")), 7)
        for e in mk("overfit"):
            eq(f"{w} overfit y", float(e["y"]), want[int(e["i"])], 1e-6)
        for kind in ("day", "lturn24", "natr_bp", "age_h"):
            items = c["strata"][kind]
            nm = f"strata.{kind}"
            eq(f"{w} {nm} n rect", len(mk(nm + ".n")), len(items))
            for e in mk(nm + ".n"):
                eq(f"{w} {nm} n", float(e["y"]), items[int(e["i"])]["n"])
            for fld, key in (("auc_cb3", "auc_cb3"), ("auc_logit", "auc_logit"), ("delta", "delta")):
                got = {int(e["i"]): e for e in mk(f"{nm}.{fld}")}
                for i, s in enumerate(items):
                    if s[key] is None:
                        eq(f"{w} {nm}.{fld}[{i}] отсутствует", i in got, False)
                    else:
                        eq(f"{w} {nm}.{fld}[{i}]", float(got[i]["y"]), s[key], 1e-6) if i in got else bad.append((f"{w} {nm}.{fld}[{i}] нет метки", None, s[key]))
            for e in mk(nm + ".auc_cb3"):
                s = items[int(e["i"])]
                eq(f"{w} {nm} k", e["k"], s["k"])
            for e in mk(nm + ".delta"):
                s = items[int(e["i"])]
                eq(f"{w} {nm} delta lo", None if "lo" not in e or e["lo"] == "" else float(e["lo"]), s["delta_lo"], 1e-6)
                eq(f"{w} {nm} delta hi", None if "hi" not in e or e["hi"] == "" else float(e["hi"]), s["delta_hi"], 1e-6)
        # таблицы
        r = TB[f"sec-sum|{dk}"]
        for m in ("CB3", "CB1", "LOGIT", "TURN"):
            eq(f"{w} таблица auc_{m}", r[f"auc_{m}"], num(c["auc"][m], 3))
        eq(f"{w} таблица delta", r["delta"], num(c["delta"]["delta"], 3, True))
        eq(f"{w} таблица ci", r["ci"], f"[{num(c['delta']['lo'], 3, True)}; {num(c['delta']['hi'], 3, True)}]")
        for m in ("CB3", "CB1", "LOGIT"):
            r = TB[f"sec-over|{dk}|{m}"]
            o = ov["models"][m]
            eq(f"{w} over {m} train", r["train"], num(o["train"], 3))
            eq(f"{w} over {m} val", r["val"], num(o["val"], 3))
            eq(f"{w} over {m} gap", r["gap"], num(o["train"] - o["val"], 3, True))
        r = TB[f"sec-u1r|{dk}"]
        u = c["u1r"]
        eq(f"{w} u1r n", r["n"], ints(u["n"]))
        eq(f"{w} u1r k", r["k"], ints(u["k"]))
        for m in ("CB3", "CB1", "LOGIT", "TURN"):
            eq(f"{w} u1r auc_{m}", r[f"auc_{m}"], num(u[m], 3))
    ver = dom["verdict"]
    eq(f"DOM {side} verdict a", "(а) выполнено" in ver[0], S["verdict"]["a"]["ok"])
    eq(f"DOM {side} verdict b", "(б) выполнено" in ver[1], S["verdict"]["b"]["ok"])

# ── 3. ход работ (TK-601): progress.json → вшитый блок → DOM (dom_progress.json из tk593_shots.js)
import os

prog_src = json.load(open(os.path.join(os.path.dirname(os.path.abspath(html_path)), "data", "progress.json"), encoding="utf-8"))
prog_emb = json.loads(re.search(r'<script id="progress-data" type="application/json">(.*?)</script>', html, re.S).group(1).replace("<\\/", "</"))
eq("progress вшитый == файл", json.dumps(prog_emb, sort_keys=True), json.dumps(prog_src, sort_keys=True))
pdom_path = os.path.join(dom_dir, "dom_progress.json")
if os.path.exists(pdom_path):
    pd_ = json.load(open(pdom_path, encoding="utf-8"))
    STAT = {"done": "готово", "running": "идёт", "waiting": "ждёт"}
    eq("DOM этапов", len(pd_["stages"]), len(prog_src["stages"]))
    for s, d in zip(prog_src["stages"], pd_["stages"]):
        w = f"DOM этап {s['id']}"
        eq(w + " id", d["id"], s["id"])
        eq(w + " статус", d["status"], s["status"])
        eq(w + " подпись статуса", d["badge"], STAT[s["status"]])
        eq(w + " шаги", ",".join(d["steps"]), ",".join(t["s"] for t in s.get("steps", [])))
        eq(w + " цифры", "|".join(d["numbers"]), "|".join(s["numbers"]))
        eq(w + " дальше", d["next"], s["next"])
        eq(w + " вопрос", d["answers"], s["answers"])
    T = prog_src["targets"]
    eq("DOM целей", len(pd_["targets"]), len(T["rows"]))
    for r, d in zip(T["rows"], pd_["targets"]):
        w = f"DOM цель {r['id']}"
        eq(w + " id", d["id"], r["id"])
        eq(w + " лонг", d["long"], r["long"])
        eq(w + " шорт", d["short"], r["short"])
        eq(w + " текст", d["what"], r["what"])
        res = [f"{k}|{r['result'][k]}" for k in ("long", "short") if r.get("result", {}).get(k)] if T.get("result_title") else []
        eq(w + " итог", " / ".join(d["res"]), " / ".join(res))
        eq(w + " итог пусто", d["none"], bool(T.get("result_title")) and not res)
    cnt = lambda k: f"{sum(r[k] == 'pass' for r in T['rows'] if r[k] != 'na')} из {sum(r[k] != 'na' for r in T['rows'])}"
    eq("DOM итог лонг", pd_["total_long"], cnt("long"))
    eq("DOM итог шорт", pd_["total_short"], cnt("short"))
    eq("DOM шапка", pd_["headline"], prog_src["headline"])
else:
    bad.append(("нет dom_progress.json", None, None))

# ── 4. эквити (TK-601): equity.json → вшитые данные → независимый пересчёт статистик → DOM (dom_equity.json, report.json из tk593_shots.js)
E = json.loads(re.search(r'<script id="equity-data" type="application/json">(.*?)</script>', html, re.S).group(1).replace("<\\/", "</"))
E_src = json.load(open(os.path.join(os.path.dirname(os.path.abspath(html_path)), "data", "equity.json"), encoding="utf-8"))
eq("equity вшитый == файл", json.dumps(E, sort_keys=True), json.dumps(E_src, sort_keys=True))
eq("equity сверок без расхождений", len(E["recon"]["bad"]), 0)
eq("equity сверок не меньше 95", E["recon"]["n"] >= 95, True)
RI = {it["what"]: it for it in E["recon"]["items"]}
EQ_NAME = {"T1": "T1 · прибыль, $", "T2": "T2 · прибыль, бп", "T4": "T4 · без жёсткого стопа", "T5": "T5 · MFE − MAE", "T6": "T6 · ранжирование", "short": "Шорт", "long": "Лонг"}
EQ_BASE = {"short": "Шорт: все сделки базы", "long": "Лонг: все сделки базы", "both": "Шорт + лонг: все сделки"}


def eq_stats(net, day, mask):
    ix = [i for i in range(len(net)) if mask is None or mask[i]]
    v = [net[i] for i in ix]
    n, sm = len(v), sum(v)
    srt = sorted(v, reverse=True)
    g = {}
    for i in ix:
        g[day[i]] = g.get(day[i], 0.0) + net[i]
    dv = sorted(g.values(), reverse=True)
    c = pk = dd = 0.0
    for x in v:
        c += x
        pk = max(pk, c)
        dd = max(dd, pk - c)
    return {"n": n, "sum": sm, "mean": sm / n, "best": srt[0], "share": srt[0] / sm if sm > 0 else None,
            "nodays": sm - sum(dv[:2]), "notrades": sm - sum(srt[:2]), "dd": dd}


def eq_fmt(st):
    return {"n": ints(st["n"]), "sum": num(st["sum"], 2, True), "mean": num(st["mean"], 2, True), "best": num(st["best"], 2, True),
            "share": "—" if st["share"] is None else num(st["share"] * 100, 1) + " %", "nodays": num(st["nodays"], 2, True), "notrades": num(st["notrades"], 2, True), "dd": num(st["dd"], 2)}


def eq_series(chart, side):
    C = E["charts"][chart]
    if side != "both":
        return C["sides"][side]
    rows = []
    for k in ("short", "long"):
        S = C["sides"][k]
        rows += [(S["t"][i], S["en"][i], S["sym"][i], S["net"][i], S["day"][i], k) for i in range(len(S["t"]))]
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    return {"t": [r[0] for r in rows], "en": [r[1] for r in rows], "sym": [r[2] for r in rows], "net": [r[3] for r in rows], "day": [r[4] for r in rows],
            "sel": {k: [i for i, r in enumerate(rows) if r[5] == k] for k in ("short", "long")}}


def eq_lines(S, side):
    n = len(S["t"])
    out = [("base", None)]
    for k, ix in S["sel"].items():
        m = [False] * n
        for i in ix:
            m[i] = True
        out.append((k, m))
    return out


edom_path = os.path.join(dom_dir, "dom_equity.json")
if os.path.exists(edom_path):
    ed = json.load(open(edom_path, encoding="utf-8"))
    for side in ("short", "long", "both"):
        d = ed[side]
        eq(f"DOM эквити {side} графиков", ",".join(d["charts"]), "ja,fit")
        for chart in ("ja", "fit"):
            S = eq_series(chart, side)
            lines = eq_lines(S, side)
            tbl = d["tables"].get(f"{chart}|{side}")
            eq(f"DOM эквити {chart}|{side} таблица есть", tbl is not None, True)
            if tbl is None:
                continue
            eq(f"DOM эквити {chart}|{side} строк", ",".join(tbl.keys()), ",".join(k for k, _ in lines))
            for k, m in lines:
                st = eq_stats(S["net"], S["day"], m)
                want = eq_fmt(st)
                for f, wv in want.items():
                    eq(f"DOM эквити {chart}|{side}|{k} {f}", tbl.get(k, {}).get(f), wv)
                eq(f"DOM эквити {chart}|{side}|{k} финал линии", d["finals"].get(f"eq.{chart}.{k}"), st["sum"], 1e-9)
            nb = sum(1 for p in d["paths"] if p["chart"].startswith(f"eq.{chart}."))
            eq(f"DOM эквити {chart}|{side} линий на графике", nb, len(lines))
            eq(f"DOM эквити {chart}|{side} база толще", [p["w"] for p in d["paths"] if p["chart"] == f"eq.{chart}.base"][0], "3.6")
    # «шорт + лонг» = сумма баз сторон
    for chart in ("ja", "fit"):
        a = eq_stats(eq_series(chart, "short")["net"], eq_series(chart, "short")["day"], None)["sum"]
        b = eq_stats(eq_series(chart, "long")["net"], eq_series(chart, "long")["day"], None)["sum"]
        eq(f"эквити {chart}: сумма баз шорт + лонг", eq_stats(eq_series(chart, "both")["net"], eq_series(chart, "both")["day"], None)["sum"], a + b, 1e-9)
    # сверка с проверенными числами TK-598 (read/pool.json и fit/fit-*.json через сверку сборщика): сумма выбранных = среднее × число
    for key, chart, side, T in (("ja short", "ja", "short", ["T1", "T2", "T4", "T5"]), ("ja long", "ja", "long", ["T6"])):
        S = eq_series(chart, side)
        for k, m in eq_lines(S, side)[1:]:
            n_want, mean_want = RI[f"{key} {k} выбрано"]["want"], RI[f"{key} {k} среднее выбранных"]["want"]
            st = eq_stats(S["net"], S["day"], m)
            eq(f"проверено {key} {k}: сделок", st["n"], n_want)
            eq(f"проверено {key} {k}: сумма = среднее × сделок", st["sum"], mean_want * n_want, 1e-9)
            eq(f"DOM проверено {key} {k}: сумма", ed[side]["tables"][f"{chart}|{side}"][k]["sum"], num(mean_want * n_want, 2, True))
    for k, m in eq_lines(eq_series("fit", "short"), "short")[1:]:
        st = eq_stats(eq_series("fit", "short")["net"], eq_series("fit", "short")["day"], m)
        eq(f"проверено fit short {k}: сумма = среднее × сделок", st["sum"], RI[f"fit short {k} среднее выбранных"]["want"] * RI[f"fit short {k} выбрано"]["want"], 1e-9)
    # наведение (report.json): сделка из подсказки есть в данных, «накоплено» базы совпадает с суммой до неё
    rp = os.path.join(dom_dir, "report.json")
    if os.path.exists(rp):
        rep = json.load(open(rp, encoding="utf-8"))
        for side in ("short", "long", "both"):
            for chart in ("ja", "fit"):
                S = eq_series(chart, side)
                cum, c = [], 0.0
                for v in S["net"]:
                    c += v
                    cum.append(c)
                fd = lambda ms: dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%d.%m %H:%M")
                for h_ in rep["equity"][side]["hover"][chart][:3]:
                    parts = [p.strip() for p in h_["text"].split("|")]
                    sym, close_s, net_s = parts[0], parts[parts.index("Закрытие") + 1].replace(" UTC", ""), parts[parts.index("Прибыль сделки") + 1].replace(" $", "")
                    base_name = EQ_BASE[side]
                    base_s = parts[parts.index(base_name) + 1]
                    hits = [i for i in range(len(S["t"])) if S["sym"][i] == sym and fd(S["t"][i]) == close_s and num(S["net"][i], 2, True) == net_s]
                    eq(f"наведение {chart}|{side} {sym} {close_s}: сделка найдена", len(hits) >= 1, True)
                    eq(f"наведение {chart}|{side} {sym} {close_s}: накоплено базы", any(num(cum[i], 2, True) == base_s for i in hits), True)
else:
    bad.append(("нет dom_equity.json", None, None))

# ── 5. форма эквити (TK-602): shape.json → вшитые данные → независимый пересчёт из массивов equity.json → DOM (dom_shape.json, report.json из tk593_shots.js)
SH = json.loads(re.search(r'<script id="shape-data" type="application/json">(.*?)</script>', html, re.S).group(1).replace("<\\/", "</"))
SH_src = json.load(open(os.path.join(os.path.dirname(os.path.abspath(html_path)), "data", "shape.json"), encoding="utf-8"))
eq("shape вшитый == файл", json.dumps(SH, sort_keys=True), json.dumps(SH_src, sort_keys=True))
eq("shape: сделки совпали с equity.json (сторона × окно)", sum(1 for r in SH["recon"] if r["ok"]), 4)
DAYMS = 86_400_000


def sh_ind(net, dc, n):
    """Независимый пересчёт мер формы: сутки по закрытию, доли лучших суток и сделок, R² прямой, плато, просадка по концу суток."""
    d = [0.0] * n
    for v, k in zip(net, dc):
        d[k] += v
    tot = sum(net)
    ds, ts = sorted(d, reverse=True), sorted(net, reverse=True)
    e, c = [], 0.0
    for v in d:
        c += v
        e.append(c)
    r2 = float(np.corrcoef(np.arange(n), e)[0, 1] ** 2)
    peak, run, longest, pk, dd = 0.0, 0, 0, 0.0, 0.0
    for v in e:
        if v > peak:
            peak, run = v, 0
        else:
            run += 1
            longest = max(longest, run)
        pk = max(pk, v)
        dd = max(dd, pk - v)
    sh = lambda lst, k: sum(lst[:k]) / tot if tot > 0 else None
    return {"n": len(net), "total": tot, "top1d_share": sh(ds, 1), "top3d_share": sh(ds, 3), "top5d_share": sh(ds, 5), "top10t_share": sh(ts, 10), "top5d_sum": sum(ds[:5]), "r2": r2, "longest_plateau": longest, "maxdd": dd, "daily": d}


def sh_fmt(m):
    p0 = lambda v: "—" if v is None else num(v * 100, 0) + " %"
    return {"n": ints(m["n"]), "sum": num(m["total"], 2, True), "s5": num(m["top5d_sum"], 2, True), "t1": p0(m["top1d_share"]), "t3": p0(m["top3d_share"]), "t5": p0(m["top5d_share"]), "u10": p0(m["top10t_share"]),
            "r2": num(m["r2"], 2), "pl": ints(m["longest_plateau"]), "dd": num(m["maxdd"], 1)}


shp = os.path.join(dom_dir, "dom_shape.json")
if os.path.exists(shp):
    sd = json.load(open(shp, encoding="utf-8"))
    rep = json.load(open(os.path.join(dom_dir, "report.json"), encoding="utf-8")) if os.path.exists(os.path.join(dom_dir, "report.json")) else None
    for side in ("short", "long"):
        d_ = sd[side]
        eq(f"DOM форма {side} графиков", ",".join(d_["cards"]), "ja,fit")
        for chart in ("ja", "fit"):
            S, Z = E["charts"][chart]["sides"][side], SH["sides"][side][chart]
            start, n = Z["start_ms"], Z["n_days"]
            eq(f"форма {chart}|{side} start_ms == equity.json", start, E["charts"][chart]["start_ms"])
            eq(f"форма {chart}|{side} сделок за окном суток", sum(1 for t in S["t"] if (t - start) // DAYMS >= n), 0)
            dc = [(t - start) // DAYMS for t in S["t"]]
            lines = [("base", None)] + [(k, set(ix)) for k, ix in S["sel"].items()]
            eq(f"DOM форма {chart}|{side} строк таблицы", ",".join(d_["tables"][f"{chart}|{side}"].keys()), ",".join(k for k, _ in lines))
            base_daily = None
            for k, m in lines:
                ix = [i for i in range(len(S["t"])) if m is None or i in m]
                ind = sh_ind([S["net"][i] for i in ix], [dc[i] for i in ix], n)
                if k == "base":
                    base_daily = ind["daily"]
                for f in ("n", "total", "top1d_share", "top3d_share", "top5d_share", "top5d_sum", "top10t_share", "r2", "longest_plateau", "maxdd"):
                    eq(f"форма {chart}|{side}|{k} {f}: вшитое == пересчёт", Z["measures"][k][f], ind[f], 1e-9)
                for f, wv in sh_fmt(ind).items():
                    eq(f"DOM форма {chart}|{side}|{k} {f}", d_["tables"][f"{chart}|{side}"][k][f], wv)
            eq(f"форма {chart}|{side} сутки: вшитое == пересчёт", max(abs(a - b) for a, b in zip(Z["daily"], base_daily)), 0.0, 1e-9)
            eq(f"форма {chart}|{side} сутки: число", len(Z["daily"]), n)
            up = sorted(range(n), key=lambda i: (-base_daily[i], i))[:5]
            dn = sorted(range(n), key=lambda i: (base_daily[i], i))[:5]
            eq(f"форма {chart}|{side} рывки", ",".join(map(str, Z["jump"])), ",".join(map(str, up)))
            eq(f"форма {chart}|{side} спады", ",".join(map(str, Z["drop"])), ",".join(map(str, dn)))
            bars = sorted(d_["bars"][chart], key=lambda b: b["i"])
            eq(f"DOM форма {chart}|{side} столбцов", len(bars), n)
            eq(f"DOM форма {chart}|{side} столбцы == сутки", max(abs(b["y"] - base_daily[b["i"]]) / max(1.0, abs(base_daily[b["i"]])) for b in bars), 0.0, 1e-6)
            eq(f"DOM форма {chart}|{side} рывков среди столбцов", ",".join(str(b["i"]) for b in bars if b["kind"] == "up"), ",".join(map(str, sorted(up))))
            eq(f"DOM форма {chart}|{side} спадов среди столбцов", ",".join(str(b["i"]) for b in bars if b["kind"] == "dn"), ",".join(map(str, sorted(dn))))
            eq(f"DOM форма {chart}|{side} полос (2 графика × 10)", len(d_["bands"][chart]), 20)
            eq(f"DOM форма {chart}|{side} финал эквити базы", d_["finals"][chart], sum(S["net"]), 1e-9)
            eq(f"форма {chart}|{side} сумма суток == сумма сделок", sum(base_daily), sum(S["net"]), 1e-9)
            if rep:
                cnt = [0] * n
                for k_ in dc:
                    cnt[k_] += 1
                for h_ in rep["shape"][side]["hover"][chart][:4]:
                    parts = [p.strip() for p in h_["text"].split("|")]
                    i = h_["want_i"]
                    fd = dt.datetime.fromtimestamp((start + i * DAYMS) / 1000, dt.timezone.utc).strftime("%d.%m")
                    tag = " · рывок" if i in up else " · спад" if i in dn else ""
                    eq(f"наведение форма {chart}|{side} сутки {i}: заголовок", parts[0], fd + tag)
                    eq(f"наведение форма {chart}|{side} сутки {i}: прибыль", parts[parts.index("Прибыль суток") + 1], num(base_daily[i], 2, True) + " $")
                    eq(f"наведение форма {chart}|{side} сутки {i}: сделок", parts[parts.index("Сделок") + 1], ints(cnt[i]))
                eq(f"наведение форма {chart}|{side}: после ухода скрыта", rep["shape"][side]["hover"][chart][4]["after_leave_shown"], False)
        if rep:
            eq(f"форма {side}: кнопка «Шорт + лонг» скрыта", rep["shape"][side]["bothHidden"], True)
            eq(f"форма {side}: нет горизонтальной прокрутки страницы", rep["shape"][side]["overflowX"] <= 0, True)
    if rep:
        eq("форма: возврат к кривой", rep["shape"]["back_to_curve"], "true")
        eq("форма: хеш #equity/long/shape", f'{rep["hash"]["#equity/long/shape"]["eq"]}|{rep["hash"]["#equity/long/shape"]["view"]}', "long|shape")
        eq("форма: хеш #equity/shape — подвид (сторона остаётся прежней при переходе по хешу)", rep["hash"]["#equity/shape"]["view"], "shape")
        eq("форма: хеш #equity/long — кривая", rep["hash"]["#equity/long"]["view"], "curve")
else:
    bad.append(("нет dom_shape.json", None, None))

print(f"проверок {n_ok + len(bad)}, совпало {n_ok}, расхождений {len(bad)}")
for b in bad[:30]:
    print("РАСХОЖДЕНИЕ", b)
sys.exit(1 if bad else 0)
