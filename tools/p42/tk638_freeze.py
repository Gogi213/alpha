#!/usr/bin/env python3
"""TK-638 / П-42 §13.36 п. 9: сборка `docs/research/freeze/tk638-frozen.json` (версия `tk638-frozen-1`) — кандидат вперёд `L_n24` отдельным файлом. `tk626-frozen.json` (коммит 1d14575e2) не меняется:
читается из git и сверяется с рабочей копией; геометрия окон / точек / долей α и вселенная берутся из него без изменений. α — из доли `α(S_base)`: 0,030 → 0,025, `L_n24` 0,005, сумма 0,05.
Числа отбора берутся из счётных JSON `docs/findings/p42-turnover-grid-2026-11-20/res/` (cells38-*, thr38, gate-638a) и `tk638_report.noise` (шумовой эталон).

  python tools/p42/tk638_freeze.py --out docs/research/freeze/tk638-frozen.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import retitr_full as rf  # noqa: E402
import tk638_report as rep  # noqa: E402

BASE_FILE, BASE_COMMIT = "docs/research/freeze/tk626-frozen.json", "1d14575e2"
RES = os.path.join(ROOT, "docs/findings/p42-turnover-grid-2026-11-20/res")
CID = "N24_q60"
FA_KEYS = "fa=%s fo=lo fv=0.5" % CID
ALPHA = {"S_base": 0.025, "S_mk1": 0.01, "L_base": 0.01, "L_n24": 0.005}
FEAT_BIN = {"md5": "69fcf7cdc74a8006e78681d29a980f68", "sha256": "6335fc947e82e16154cbfd6ccfa2e197ccfae3138f19694ef70f31126659e69a", "path": "/data/p42-tk638/bin/alpha-feat.69fcf7cd",
            "source": "src/breakout/feat/tcount.rs (+ tcount_tests.rs), команда `alpha-feat tcount`"}
MONTHS = ["июль", "август", "сентябрь", "октябрь", "01–13.11"]


def sha_lf(b):
    return hashlib.sha256(b.replace(b"\r\n", b"\n")).hexdigest()


def jl(name):
    return json.load(open(os.path.join(RES, name), encoding="utf-8"))


def res_sha(name):
    return sha_lf(open(os.path.join(RES, name), "rb").read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    blob = subprocess.run(["git", "show", "%s:%s" % (BASE_COMMIT, BASE_FILE)], cwd=ROOT, capture_output=True, check=True).stdout
    if sha_lf(blob) != sha_lf(open(os.path.join(ROOT, BASE_FILE), "rb").read()):
        sys.exit("tk626-frozen.json в рабочей копии ≠ коммиту %s" % BASE_COMMIT)
    fz = json.loads(blob.decode("utf-8"))
    assert abs(sum(ALPHA.values()) - 0.05) < 1e-12 and abs(ALPHA["S_base"] + ALPHA["L_n24"] - fz["alpha"]["S_base"]) < 1e-12
    base_l = fz["cells"]["L_base"]
    keys = "%s %s" % (base_l["keys"], FA_KEYS)
    cl, co, thr, gate = jl("cells38-h775-L.json"), jl("cells38-o566-L.json"), jl("thr38.json")["L"]["N24"], jl("gate-638a.json")
    assert gate["ok"] and gate["keys"] == keys and gate["bin_md5"] == fz["binary"]["md5"], "ворота G-638a ≠ ключам / бинарю заморозки"
    N = int(thr["q"]["0.60"])
    assert N == 220000
    L, b = cl["ladders"][CID], cl["base"]
    se, nz = rep.noise(L["sharpe"])
    sel = {
        "mark": "найдено на этих данных",
        "grid": "71 клетка на сторону (63 клетки D × H + 8 клеток лестниц n60 / n24), h775, 01.07–13.11 (136 сут.); лонг N24 ≥ %d выбран по P&L из лестницы, прирост по построению отбора" % N,
        "cell_h775": {"trades": L["n"], "sum_usd": round(L["sum"], 2), "mean_usd": round(L["mean"], 3), "sharpe": round(L["sharpe"], 2), "lo90": round(L["lo90"], 2), "hi90": round(L["hi90"], 2),
                      "months": {m: {"trades": t, "sum_usd": round(s, 1)} for m, (t, s) in zip(MONTHS, L["months"])}, "rr": round(L["rr"]["rr"], 2),
                      "stop_pct_margin": round(L["rr"]["stops"]["mean_loss_pct"], 1), "maxdd_usd": round(L["maxdd"], 1), "in_base_trades": L["in_base"]},
        "base_L_h775": {"trades": b["n"], "sum_usd": round(b["sum"], 2), "mean_usd": round(b["mean"], 3), "sharpe": round(b["sharpe"], 2)},
        "noise": {"sharpe_se": round(se, 2), "n_cells": rep.N_VIEWED, "noise_max_sharpe": round(nz["noise_bar"], 2), "deflated_sharpe": round(nz["deflated"], 2), "t_deflated": round(nz["t_deflated"], 2),
                  "survives_5pct": bool(nz["survives"]),
                  "note": "по 71 перекрывающимся клеткам; SE — для 136 независимых суток, без поправки на автокорреляцию и хвосты (`selection_bias.py`, `tk638_report.noise`)"},
        "plateau_N24_long": {k: {"N": int(thr["q"]["%.2f" % q]), "trades": cl["ladders"][k]["n"], "sum_usd": round(cl["ladders"][k]["sum"], 1), "mean_usd": round(cl["ladders"][k]["mean"], 2),
                                 "sharpe": round(cl["ladders"][k]["sharpe"], 2)} for k, q in (("N24_q20", 0.2), ("N24_q40", 0.4), (CID, 0.6), ("N24_q80", 0.8))},
        "repeat_o566": {"trades": co["ladders"][CID]["n"], "sum_usd": round(co["ladders"][CID]["sum"], 1), "sharpe": round(co["ladders"][CID]["sharpe"], 2),
                        "note": "те же сигналы базы (o566 содержит их), не независимая проверка"},
        "plateau_note": "все четыре ступени N24 выше базы по шарпу (2,90…5,11 против 1,96) и по $ на сделку (0,73…1,63 против 0,45), по сумме выше базы три из четырёх; пик шарпа — на q60 (5,11), соседи q40 / q80 — 3,10 / 3,30",
    }
    sh = fz["point_shares"]
    out = {
        "version": "tk638-frozen-1",
        "protocol": "П-42 §13.36 п. 9 (кандидат вперёд «найдено на этих данных») + §13.35 п. 10, §13.28.1 п. 3 и п. 8",
        "decision": "решение координатора `thr_ty6ae9r46y` 20.11.2026: заморозить `L_n24` кандидатом вперёд; условия критика `thr_sia32k2tr5` (TK-636 п. 10): отдельный файл ДО первого чтения суток ≥ 17.11, "
                    "α из доли α(S_base), клетка задана полностью, проверка вперёд — первая независимая",
        "read_before_commit": "суток ≥ 17.11 этим тредом не читалось; маркеров чтения `READ-*` в /data/p42-tk616/fwd и /data/p42-tk626 нет (20.11)",
        "base_freeze": {"file": BASE_FILE, "commit": BASE_COMMIT, "sha256": sha_lf(blob), "version": fz["version"], "untouched": True,
                        "note": "файл в git не меняется; для решения по `S_base` действует α этого файла (0,025), α(S_mk1) и α(L_base) те же"},
        "binary": fz["binary"],
        "feature_binary": FEAT_BIN,
        "universe": fz["universe"],
        "cells": {"L_n24": {"side": "L", "base_cell": "L_base", "base_keys": base_l["keys"], "base_vid": base_l["vid"], "keys": keys, "vid": rf.Req("L", keys, False).vid, "family": False,
                            "candidate": True}},
        "filter": {
            "name": CID, "feature": "n24", "rule": "торгуются только сигналы базы `L_base` с n24 ≥ N; сигнал с n24 < N пропускается (клетка = база с таблицей `--ftab`, не новый пул)",
            "N": N, "N_unit": "число записей aggTrades монеты за окно; ОДНО абсолютное число для всех монет и всех точек, вперёд не пересчитывается",
            "N_origin": "общий квантиль 0,6 по n24 сигналов базы h775 лонг в зачётных диапазонах (914 кандидатов, три окна), метод linear, 2 значащие цифры (%g → %d; по всем строкам таблиц 210 000)" % (thr["q_raw"]["0.60"], N),
            "window": "[(t−1440)·60000, t·60000) мс, t = ⌊sig_ms / 60000⌋: закрытые минуты строго раньше минуты сигнала (как пул `tick::candidates`)",
            "missing_data": "нет файла суток окна → m24 = 1, значение пусто и ПРОХОДИТ (1,0); число таких строк печатается",
            "ftab": "CSV `sym,sig_ms,N24_q60`: 1,0 — пропущен (n24 ≥ N или m24), 0,0 — исключён; ключ (монета, sig_ms); движок `--ftab <файл>` с `%s` (исключает значение < 0,5)" % FA_KEYS,
            "per_point": "таблица строится по кандидатам базы `L_base` точки (окна `w` точки) тем же `alpha-feat tcount` (md5 выше) ДО запуска клетки; sha256 таблиц — в маркер чтения; G8: no_key = 0 и dropped = пересчёту",
            "tool_note": "`tk616_forward.py` на коммите заморозки клеток с `--ftab` не знает (0 упоминаний): расширение и его тесты на синтетике — отдельным коммитом ДО чтения L1 (30.11); правил этого файла не меняет",
        },
        "alpha": ALPHA,
        "alpha_split_rule": "§13.28.1 п. 3: кандидаты берут α только из доли α(S_base): 0,030 → 0,025 (`S_base`) + 0,005 (`L_n24`); α(S_mk1) = 0,01 и α(L_base) = 0,01 без изменений; сумма 0,05",
        "alpha_k": {c: {p: round(ALPHA[c] * v, 6) for p, v in sh.items()} for c in ALPHA},
        "decision_rule": "`tk616_forward.decide` (§13.28 п. 7 дословно): подтверждено — p ≤ α_k, ΣS > 0, среднее > 0 без 2 лучших суток и без 2 лучших монет, (ΣS − B)/N > 0, n ≥ 60; опровергнуто — "
                         "только на L5; иначе «не ясно». Одно прочтение на точку, одна клетка, одна решающая вселенная `all`",
        "point_shares": sh, "points": fz["points"], "windows": fz["windows"],
        "selection": sel,
        "gates": {"G-638a": {"what": "`L_n24` замороженным бинарём `alpha-mk.396989e9` побайтно = прогон TK-638 (`alpha-master a112684a`): long-{trades,daily,coinday,entryrej}.csv × wa / wb / wc, h775; суток ≥ 14.11 не читается",
                             "ok": gate["ok"], "file": "docs/findings/p42-turnover-grid-2026-11-20/res/gate-638a.json", "results_sha256": res_sha("gate-638a.json"),
                             "ftab_sha256": {w: v["ftab_sha256"] for w, v in gate["windows"].items()}, "code": "tools/p42/tk638_gate.py"},
                  "thresholds_sha256": res_sha("thr38.json")},
        "ref": "docs/research/P-42-level-breakout.md §13.36; docs/findings/p42-turnover-grid-2026-11-20.md; kb/task/TK-638.md",
    }
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("записано", a.out, "vid", out["cells"]["L_n24"]["vid"], "alpha_k L_n24", out["alpha_k"]["L_n24"])


if __name__ == "__main__":
    main()
