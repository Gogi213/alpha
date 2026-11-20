#!/usr/bin/env python3
"""П-42 §13.38 (TK-641): сборка `docs/research/freeze/tk641-frozen.json` — кандидат вперёд `L_mk2` (`L_base` + `mk=2`), α 0,005 из доли α(S_base) (0,025 → 0,020).
  python3 tools/p42/tk641_freeze.py      (ПК; читает только git-файлы: tk626 / tk638 заморозки и `docs/findings/p42-lmk2-forward-2026-11-20/res/tk641-report.json`)
Цепочка α: tk626 → tk638 → tk641; файл записывает sha256 обоих предыдущих (как `tk616_forward.sha256_text`)."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import retitr_full as rf  # noqa: E402
import tk616_forward as fw  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
FZ_DIR = os.path.join(REPO, "docs", "research", "freeze")
REPORT = os.path.join(REPO, "docs", "findings", "p42-lmk2-forward-2026-11-20", "res", "tk641-report.json")
OUT = os.path.join(FZ_DIR, "tk641-frozen.json")
CELL, TAKE = "L_mk2", 0.005


def r(x, nd=3):
    return round(float(x), nd)


def build(fz, fz_sha, cand, cand_sha, rep, rep_sha):
    base = fz["cells"]["L_base"]
    keys = base["keys"].replace(" t24=12", " mk=2 t24=12")
    vid = rf.Req("L", keys, False).vid
    alpha = dict(cand["alpha"])
    alpha["S_base"] = round(alpha["S_base"] - TAKE, 10)
    alpha[CELL] = TAKE
    shares = fz["point_shares"]
    g = fz["gates"]["G-626c"]
    desc = lambda c: {"trades": rep["desc"][c]["n"], "sum_usd": r(rep["desc"][c]["sum"], 2), "mean_usd": r(rep["desc"][c]["mean"]), "usd_per_day": r(rep["desc"][c]["per_day"], 2),  # noqa: E731
                      "sharpe": r(rep["desc"][c]["sharpe"], 2), "lo90": r(rep["desc"][c]["lo90"]), "hi90": r(rep["desc"][c]["hi90"]), "rr": r(rep["desc"][c]["rr"], 2),
                      "stops": rep["desc"][c]["stops"]["n"], "maxdd_usd": r(rep["desc"][c]["maxdd"], 1),
                      "months": {m["month"]: {"trades": m["n"], "sum_usd": r(m["sum"], 1)} for m in rep["desc"][c]["months"]}}
    ov = {k: {a: (r(b) if isinstance(b, float) else b) for a, b in v.items()} for k, v in rep["overlap"].items()}
    nz = rep["noise"]
    return {
        "version": "tk641-frozen-1",
        "protocol": "П-42 §13.38 (TK-641): кандидат вперёд `L_mk2`; аудит TK-637 строка 4; правило деления α §13.28.1 п. 3",
        "decision": "решение координатора `thr_ty6ae9r46y` (В-165): `L_mk2` прошёл предзаписанный G_mk на 775 (G-626c) — проверить вперёд отдельным кандидатом; PASS критика `thr_sia32k2tr5` на `d56bcce2b` (условия C1–C3)",
        "read_before_commit": "суток ≥ 17.11 этим тредом не читалось; маркеров `READ-*` в /data/p42-tk616/fwd и /data/p42-tk626 нет (охрана `tk641_report.guards`, 20.11)",
        "base_freeze": {"file": "docs/research/freeze/tk626-frozen.json", "version": fz["version"], "sha256": fz_sha, "untouched": True},
        "prev_candidate": {"file": "docs/research/freeze/tk638-frozen.json", "version": cand["version"], "sha256": cand_sha, "untouched": True,
                           "note": "α этого файла перекрывают α tk638 и tk626 (цепочка tk626 → tk638 → tk641)"},
        "binary": fz["binary"], "universe": fz["universe"], "point_shares": shares, "points": fz["points"], "windows": fz["windows"],
        "cells": {CELL: {"side": "L", "base_cell": "L_base", "base_keys": base["keys"], "base_vid": base["vid"], "keys": keys, "vid": vid, "family": False, "candidate": True}},
        "alpha": alpha,
        "alpha_k": {k: {p: round(v * s, 10) for p, s in shares.items()} for k, v in alpha.items()},
        "alpha_split_rule": "§13.28.1 п. 3: кандидаты берут α только из доли α(S_base): 0,025 (tk638) → 0,020 (`S_base`) + 0,005 (`L_mk2`); `L_n24` 0,005, `S_mk1` 0,01, `L_base` 0,01 без изменений; сумма 0,05",
        "decision_rule": "`tk616_forward.decide` (§13.28 п. 7 дословно), одно прочтение на точку, решающая вселенная `all`; L1 описательно",
        "selection": {
            "mark": "найдено на этих данных",
            "gate": {"name": "G-626c (G_mk §13.28 п. 6 на 775)", "sum_usd": r(g["cells"][CELL]["sum"], 2), "trades": g["cells"][CELL]["n"], "mean_usd": r(g["cells"][CELL]["mean"]),
                     "p": r(g["cells"][CELL]["p"], 4), "holm_threshold": r(g["cells"][CELL]["holm_threshold"], 4), "positive_months": g["cells"][CELL]["positive_months"], "pass": g["cells"][CELL]["pass"]},
            "describe_h775": {c: desc(c) for c in ("L_mk2", "L_base", "L_n24", "S_base")},
            "overlap": ov,
            "noise": {"n_cells": nz["n_tried"], "cells": "G_mk на 566 (TK-616) + на 775 (G-626c): L_mk1, L_mk2, S_mk1, S_mk2 × 2", "sharpe": r(nz["sharpe"], 2), "sharpe_se": r(nz["se"], 2),
                      "noise_max_sharpe": r(nz["noise_bar"], 2), "deflated_sharpe": r(nz["deflated"], 2), "t_deflated": r(nz["t_deflated"], 2), "survives_5pct": nz["survives"],
                      "note": "SE — для 136 независимых суток, без поправки на автокорреляцию и хвосты (`selection_bias.py`)"},
        },
        "gates": {"G-626c": {"results_sha256": g["results_sha256"], "outputs": "/data/p42-tk626/gmk/out/<wa|wb|wc>/%s/long-*" % vid},
                  "report_sha256": rep_sha, "report": "docs/findings/p42-lmk2-forward-2026-11-20/res/tk641-report.json (`tools/p42/tk641_report.py`)"},
        "tool_note": "C1–C3 критика: `tk616_forward.py` требует этот файл при его наличии в цепочке (нет — остановка, без возврата к α tk638); `--no-candidate2` — α применяются, `L_mk2` не читается (в маркер); C3 — `replay-mk2` побайтно против выходов G-626c; отдельным коммитом до L1 (30.11)",
        "ref": "docs/research/P-42-level-breakout.md §13.38; docs/findings/p42-lmk2-forward-2026-11-20.md; kb/task/TK-641.md",
    }


def main():
    fzp, cp = os.path.join(FZ_DIR, "tk626-frozen.json"), os.path.join(FZ_DIR, "tk638-frozen.json")
    fz = fw.load_freeze(fzp)
    cand = fw.load_candidate(cp, fz, fzp)
    rep = json.load(open(REPORT, encoding="utf-8"))
    out = build(fz, fw.sha256_text(fzp), cand, fw.sha256_text(cp), rep, fw.sha256_text(REPORT))
    assert abs(sum(out["alpha"].values()) - 0.05) < 1e-12 and out["alpha"]["S_base"] == 0.02
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(OUT, out["cells"][CELL]["vid"], out["alpha"])


if __name__ == "__main__":
    main()
