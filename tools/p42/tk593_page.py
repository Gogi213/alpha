"""П-42 TK-593: сборка страницы CatBoost (один HTML, данные внутри) из результатов TK-592 и кривых tk593_compute.py.

Использование: <venv>/bin/python tk593_page.py <tree TK-592> <pairs TK-592> <curves.json> <template.html> <выход: каталог страницы> [progress.json] [equity.json] [shape.json] [features.json]
Пишет <каталог>/index.html и <каталог>/data/page_data.json. Критерии (а)/(б) считаются здесь по правилам §13.11 протокола П-42.
TK-601: блок «Ход работ» берётся из progress.json (по умолчанию <каталог>/data/progress.json), проверяется по ml_progress.schema.json рядом со скриптом
и вшивается в index.html (страница статическая: после правки JSON — запустить сборку ещё раз).
TK-601: вкладка «Эквити» — данные из equity.json (по умолчанию <каталог>/data/equity.json; строит tk601_equity.py), вшиваются так же.
TK-602: подвид «Форма» вкладки «Эквити» — данные из shape.json (по умолчанию <каталог>/data/shape.json; строит tk602_shape.py), вшиваются так же.
TK-606: вкладка «Признаки» — данные из features.json (по умолчанию <каталог>/data/features.json; источник — tools/p42/ml_features.json, схема ml_features.schema.json,
плюс проверки: ссылки src ∈ sources, имена признаков не повторяются, у набора «идёт» нет цифр), вшиваются так же.
TK-624: блок «Где мы» (progress.json → where) и вид «Базы» вкладки «Эквити» — данные из bases.json (по умолчанию <каталог>/data/bases.json; строит tk624_table.py:
суточные ряды L_base / S_base (TK-599) и S_mk1 (TK-616), $ при ставке 500 $); итоги ряда сверяются с принятыми отчётами, вшиваются так же.
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
shape_path = sys.argv[8] if len(sys.argv) > 8 else f"{out_dir}/data/shape.json"
features_path = sys.argv[9] if len(sys.argv) > 9 else f"{out_dir}/data/features.json"
bases_path = sys.argv[10] if len(sys.argv) > 10 else f"{out_dir}/data/bases.json"
universe_path = sys.argv[11] if len(sys.argv) > 11 else f"{out_dir}/data/universe.json"
# итоги баз по принятым отчётам: (Σ $, сделок) — p42-maker-entry-2026-11-19.md (L_base, S_base, S_mk1) и p42-retitration-full-2026-11-19.md
ACCEPTED_BASES = {"L_base": (7.06, 621), "S_base": (259.14, 431), "S_mk1": (248.05, 372)}
BAR = 0.02
DIRS = ("A->B", "B->A")
SET = "f12"


def check_features(f):
    """Смысловые проверки features.json сверх схемы (TK-606)."""
    names = [x["name"] for s in f["sets"] for g in s["groups"] for x in g["features"]]
    dup = sorted({n for n in names if names.count(n) > 1})
    assert not dup, f"features.json: имя признака повторяется: {dup}"
    bad = sorted({(x["name"], k) for s in f["sets"] for g in s["groups"] for x in g["features"] for k in x["src"] if k not in f["sources"]})
    assert not bad, f"features.json: src не из sources: {bad}"
    assert len({s["id"] for s in f["sets"]}) == len(f["sets"]), "features.json: id набора повторяется"
    for s in f["sets"]:
        assert s["status"] == "done" or not s["numbers"], f"features.json: у набора {s['id']} со статусом «идёт» цифр быть не должно"
        assert s["numbers"] == [] or s["reports"], f"features.json: у набора {s['id']} есть цифры, но нет отчёта"


def check_bases(b, progress):
    """Смысловые проверки bases.json и связи с progress.json (TK-624)."""
    assert b["days"] == 136 and sum(b["month_len"]) == 136 and len(b["month_names"]) == len(b["month_len"]), "bases.json: окно не 136 суток"
    assert sorted(b["cells"]) == sorted(ACCEPTED_BASES), f"bases.json: клетки {sorted(b['cells'])}"
    for k, (tot, n) in ACCEPTED_BASES.items():
        c = b["cells"][k]
        assert len(c["daily_sum"]) == 136 and len(c["daily_n"]) == 136, f"bases.json: {k} не 136 суток"
        s = sum(c["daily_sum"])
        assert abs(s - tot) < 0.006, f"bases.json: {k} Σ {s:.4f} ≠ принятого {tot}"
        assert sum(c["daily_n"]) == n, f"bases.json: {k} сделок {sum(c['daily_n'])} ≠ принятого {n}"
        assert abs(c["rows"][-1]["sum"] - s) < 1e-6 and c["rows"][-1]["trades"] == n, f"bases.json: {k} итоговая строка не равна ряду"
    ids = [r["id"] for r in progress["where"]["rows"]]
    assert len(ids) == len(set(ids)) and set(ids) <= set(b["cells"]), f"progress.json where.rows: {ids}"
    seen, last = set(), None
    for s_ in progress["stages"]:
        g = s_.get("group")
        if g != last:
            assert g not in seen, f"progress.json: группа «{g}» повторяется не подряд"
            seen.add(g)
            last = g
    assert len({s_["id"] for s_ in progress["stages"]}) == len(progress["stages"]), "progress.json: id этапа повторяется"


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
    shape = json.load(open(shape_path, encoding="utf-8"))
    assert all(r["ok"] for r in shape["recon"]), "shape.json: сделки не совпали с equity.json"
    sblob = json.dumps(shape, ensure_ascii=False, allow_nan=False, separators=(",", ":")).replace("</", "<\\/")
    features = json.load(open(features_path, encoding="utf-8"))
    jsonschema.validate(features, json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ml_features.schema.json"), encoding="utf-8")))
    check_features(features)
    fblob = json.dumps(features, ensure_ascii=False, allow_nan=False, separators=(",", ":")).replace("</", "<\\/")
    bases = json.load(open(bases_path, encoding="utf-8"))
    check_bases(bases, progress)
    bblob = json.dumps(bases, ensure_ascii=False, allow_nan=False, separators=(",", ":")).replace("</", "<\\/")
    uni = json.load(open(universe_path, encoding="utf-8"))
    assert [u["id"] for u in uni["universes"]] == ["U0", "U1", "U2", "U3", "U4"] and uni["days"] == 136, "universe.json: вселенные/окно"
    for side in ("long", "short"):
        for u in uni["universes"]:
            assert len(uni["sides"][side][u["id"]]["daily_sum"]) == 136 and len(uni["sides"][side][u["id"]]["rows"]) == 6, f"universe.json: {side} {u['id']}"
    for side, base in (("long", "L_base"), ("short", "S_base")):   # U0 = базы TK-599
        assert abs(sum(uni["sides"][side]["U0"]["daily_sum"]) - ACCEPTED_BASES[base][0]) < 0.006, f"universe.json: U0 {side} != база TK-599"
    ublob = json.dumps(uni, ensure_ascii=False, allow_nan=False, separators=(",", ":")).replace("</", "<\\/")
    pblob = json.dumps(progress, ensure_ascii=False, allow_nan=False, separators=(",", ":")).replace("</", "<\\/")
    assert "/*__DATA__*/" in tpl and "/*__PROGRESS__*/" in tpl and "/*__EQUITY__*/" in tpl and "/*__SHAPE__*/" in tpl and "/*__FEATURES__*/" in tpl and "/*__BASES__*/" in tpl and "/*__UNIVERSE__*/" in tpl
    os.makedirs(f"{out_dir}/data", exist_ok=True)
    open(f"{out_dir}/index.html", "w", encoding="utf-8").write(tpl.replace("/*__DATA__*/", blob).replace("/*__PROGRESS__*/", pblob).replace("/*__EQUITY__*/", eblob).replace("/*__SHAPE__*/", sblob).replace("/*__FEATURES__*/", fblob).replace("/*__BASES__*/", bblob).replace("/*__UNIVERSE__*/", ublob))
    json.dump(data, open(f"{out_dir}/data/page_data.json", "w", encoding="utf-8"), ensure_ascii=False)
    print("index.html", len(tpl) + len(blob) + len(pblob) + len(eblob) + len(sblob) + len(fblob), "байт; ход работ", progress["updated"], f"({len(progress['stages'])} этапов); признаки", features["updated"], f"({len(features['sets'])} наборов); (а)", {s: data["sides"][s]["verdict"]["a"]["ok"] for s in data["sides"]}, flush=True)


if __name__ == "__main__":
    main()
