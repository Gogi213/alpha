"""П-42 TK-593: сборка страницы CatBoost (один HTML, данные внутри) из результатов TK-592 и кривых tk593_compute.py.

Использование: <venv>/bin/python tk593_page.py <tree TK-592> <pairs TK-592> <curves.json> <template.html> <выход: каталог страницы> [progress.json] [equity.json]
Пишет <каталог>/index.html и <каталог>/data/page_data.json. Критерии (а)/(б) считаются здесь по правилам §13.11 протокола П-42.
TK-601: блок «Ход работ» берётся из progress.json (по умолчанию <каталог>/data/progress.json), проверяется по ml_progress.schema.json рядом со скриптом
и вшивается в index.html (страница статическая: после правки JSON — запустить сборку ещё раз).
TK-601: вкладка «Эквити» — данные из equity.json (по умолчанию <каталог>/data/equity.json; строит tk601_equity.py), вшиваются так же.
"""
from __future__ import annotations

import json
import os
import sys

import jsonschema
import pandas as pd

tree_dir, pairs_dir, curves_path, tpl_path, out_dir = sys.argv[1:6]
progress_path = sys.argv[6] if len(sys.argv) > 6 else f"{out_dir}/data/progress.json"
equity_path = sys.argv[7] if len(sys.argv) > 7 else f"{out_dir}/data/equity.json"
BAR = 0.02
DIRS = ("A->B", "B->A")
SET = "f12"


def main():
    auc = pd.read_csv(f"{tree_dir}/tree_auc.csv")
    delta = pd.read_csv(f"{tree_dir}/tree_delta.csv")
    perm = pd.read_csv(f"{tree_dir}/tree_perm.csv")
    shap = pd.read_csv(f"{tree_dir}/tree_shap.csv")
    core = json.load(open(f"{pairs_dir}/core.json"))
    psum = json.load(open(f"{pairs_dir}/summary.json"))
    cur = json.load(open(curves_path))
    assert cur["meta"]["day_last"] < 74, "отложенные сутки не показываем"
    assert sorted(core["core_used"]) == sorted(cur["meta"]["features"]), "ядро не равно 12 признакам: страница рассчитана на f12"
    data = {"meta": {"halfA": [14, 43], "halfB": [45, 73], "embargo_day": 44, "core_n": len(core["core_used"]), "core_fallback": bool(core["core_empty_fallback_all12"]),
                     "day_first": cur["meta"]["day_first"], "day_last": cur["meta"]["day_last"]},
            "roc_fpr": cur["roc_fpr"], "sides": {}}
    for side in ("long", "short"):
        dirs = {}
        for dk in DIRS:
            c = cur["cells"][side][dk]
            a = auc[(auc.side == side) & (auc.dir == dk)]
            m = {r.model: float(r.auc) for r in a[a.set == SET].itertuples()}
            m["TURN"] = float(a[a.model == "TURN"].auc.iloc[0])
            dl = delta[(delta.side == side) & (delta.dir == dk) & (delta.set == SET) & (delta.pair == "CB3-LOGIT")].iloc[0]
            pr = perm[(perm.side == side) & (perm.dir == dk) & (perm.set == SET)][["feature", "auc_drop"]]
            sh = shap[(shap.side == side) & (shap.dir == dk) & (shap.set == SET)].sort_values("mean_abs", ascending=False).head(10)
            dirs[dk] = {"train_half": c["train_half"], "val_half": c["val_half"], "n_T": c["n_T"], "k_T": c["k_T"], "n_S": c["n_S"], "k_S": c["k_S"], "auc": m,
                        "delta": {"delta": float(dl.delta), "lo": float(dl.lo), "hi": float(dl.hi)},
                        "roc": c["roc"], "deciles": c["deciles"], "base_y": c["base_y"], "base_c": c["base_c"], "tertile_cut_q": c["tertile_cut_q"],
                        "perm": pr.to_dict("records"), "shap": sh[["f1", "f2", "mean_abs"]].to_dict("records"),
                        "lc": c["lc"], "overfit": c["overfit"], "strata": c["strata"], "u1r": c["u1r"]}
        d = [dirs[k]["delta"] for k in DIRS]
        a_ok = all(x["delta"] >= BAR and x["lo"] > 0 for x in d)
        conf = [int(psum[f"{side}_{k[0]}to{k[-1]}"]["confirmed"]) for k in DIRS]
        if min(conf) >= 1:
            raise NotImplementedError("подтверждённые клетки есть: (б) требует E1 ≥ +2 п.п. в обеих половинах и знак на U1r — не считается здесь")
        data["sides"][side] = {"dirs": dirs, "verdict": {"a": {"ok": a_ok, "d": d}, "b": {"ok": False, "confirmed": conf}}}
    progress = json.load(open(progress_path, encoding="utf-8"))
    schema = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ml_progress.schema.json"), encoding="utf-8"))
    jsonschema.validate(progress, schema)
    tpl = open(tpl_path, encoding="utf-8").read()
    blob = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).replace("</", "<\\/")
    equity = json.load(open(equity_path, encoding="utf-8"))
    assert not equity["recon"]["bad"], "equity.json: есть расхождения со сверкой"
    eblob = json.dumps(equity, ensure_ascii=False, allow_nan=False, separators=(",", ":")).replace("</", "<\\/")
    pblob = json.dumps(progress, ensure_ascii=False, allow_nan=False, separators=(",", ":")).replace("</", "<\\/")
    assert "/*__DATA__*/" in tpl and "/*__PROGRESS__*/" in tpl and "/*__EQUITY__*/" in tpl
    os.makedirs(f"{out_dir}/data", exist_ok=True)
    open(f"{out_dir}/index.html", "w", encoding="utf-8").write(tpl.replace("/*__DATA__*/", blob).replace("/*__PROGRESS__*/", pblob).replace("/*__EQUITY__*/", eblob))
    json.dump(data, open(f"{out_dir}/data/page_data.json", "w", encoding="utf-8"), ensure_ascii=False)
    print("index.html", len(tpl) + len(blob) + len(pblob) + len(eblob), "байт; ход работ", progress["updated"], f"({len(progress['stages'])} этапов); (а)", {s: data["sides"][s]["verdict"]["a"]["ok"] for s in data["sides"]}, flush=True)


if __name__ == "__main__":
    main()
