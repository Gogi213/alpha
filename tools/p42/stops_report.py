"""П-42 §13.19 (TK-591): отчёт по клеткам «стопы, выходы, два входа от уровня» поверх `ow_e` (сетка `s19`) и по сборке принятых значений (`s19c`).
По клетке и стороне: сделок, сумма $, среднее на сделку, доля в плюсе, $/сут. по половинам A и B со 95 %-интервалом, виды выходов, `lot2_*` (e2),
парная разность по суткам против `ow_e` (A, B, 60; блок 4) и её чтение (§13.11), вердикт §10 при накопленном m; выбор по правилу §13.19
(семейство → клетка с max Δ_A при ≥ 70 сделок в A и в B, принято при Δ_B > 0). Медиана результатов не пишется.

Запуск на srv (566):

  P42_ROOT=/data/p42-charts/pkroot P42_FQ_COL=first_day_ge_12M python3 stops_report.py gate            # ворота gate20 против v18 (e16)
  P42_ROOT=/data/p42-charts/pkroot P42_FQ_COL=first_day_ge_12M python3 stops_report.py grid [extra]    # сетка s19 (+1 к m сторон при ls20)
  P42_ROOT=/data/p42-charts/pkroot P42_FQ_COL=first_day_ge_12M python3 stops_report.py comb [extra]    # сборка s19c: comb_long, comb_short
"""
import collections
import filecmp
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import detstat  # noqa: E402
import report_tick  # noqa: E402
import ws_report as wr  # noqa: E402
from lib import A, B, ND, SIDES  # noqa: E402
from report import fq_days  # noqa: E402

ROOT = "/data/p42-t566/out"
BASE_M = {"long": 694, "short": 687}  # накопленный Холм: §13.16 678/667 + клетки 15/19 + сборка 1 (+1 при ls20)
CTRL = "ow_e"
FAMILIES = [
    ("ls", ["ls05", "ls10", "ls20"], "стоп за краем зоны"),
    ("tx", ["tx5_x1", "tx5_x3", "tx20_x1", "tx20_x3"], "выход по времени"),
    ("rt", ["rt01_2", "rt01_20", "rt02_2", "rt02_20"], "выход при возврате"),
    ("bm", ["bm1", "bm2", "bm3"], "безубыток"),
    ("e2", ["e2_10", "e2_05"], "два входа"),
    ("es", ["es15", "es10", "es05", "es15_e2_05"], "ближе к уровню (шорт)"),
]
FAM_SIDES = {"es": ("short",)}
GATE_CELLS = ["b0", "b0pk", "ow", "b0pk_e", "ow_e"]
KINDS = ("trades", "daily", "coinday", "detail", "entryrej")
MIN_TRADES = 70
# ключи клетки → ключи варианта в движке (для сборки)
CELL_KEYS = {
    "ls05": {"ls": "0.5"}, "ls10": {"ls": "1"}, "ls20": {"ls": "2"},
    "tx5_x1": {"tt": "5", "tb": "0.1"}, "tx5_x3": {"tt": "5", "tb": "0.3"}, "tx20_x1": {"tt": "20", "tb": "0.1"}, "tx20_x3": {"tt": "20", "tb": "0.3"},
    "rt01_2": {"rf": "0.1", "rs": "2"}, "rt01_20": {"rf": "0.1", "rs": "20"}, "rt02_2": {"rf": "0.2", "rs": "2"}, "rt02_20": {"rf": "0.2", "rs": "20"},
    "bm1": {"bm": "1"}, "bm2": {"bm": "2"}, "bm3": {"bm": "3"},
    "e2_10": {"e2": "0.1"}, "e2_05": {"e2": "0.05"},
    "es15": {"e": "0.15"}, "es10": {"e": "0.1"}, "es05": {"e": "0.05"}, "es15_e2_05": {"e": "0.15", "e2": "0.05"},
}
BASE_LINE = "lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0"


def fname(side, kind):
    return "%s-%s.%s" % (side, kind, "jsonl" if kind == "detail" else "csv")


def gate_cmp(new, old):
    out = {}
    for side in SIDES:
        for kind in KINDS:
            f = fname(side, kind)
            a, b = os.path.join(new, f), os.path.join(old, f)
            if not os.path.exists(a) and not os.path.exists(b):
                out[f] = True  # файла нет ни там, ни там (entryrej у b0 / b0pk без ew)
            elif os.path.exists(a) != os.path.exists(b):
                out[f] = False
            else:
                out[f] = filecmp.cmp(a, b, shallow=False)
    return out


def mean(v):
    return float(np.mean(v)) if len(v) else float("nan")


def tstats(vdir, side):
    tr = wr.read_tr(vdir, side)
    nets = [float(r["net"]) for r in tr]
    kinds = collections.Counter(r["kind"] for r in tr)
    return {"n": len(nets), "sum": sum(nets), "mean": mean(nets), "pos_share": (sum(1 for x in nets if x > 0) / len(nets)) if nets else float("nan"),
            "kinds": {k: kinds.get(k, 0) for k in ("stop", "be", "trail", "tx", "rt", "forced")}}


def do_gate():
    out = {}
    for c in GATE_CELLS:
        new, old = os.path.join(ROOT, "gate20", c), os.path.join(ROOT, "e16", c)
        if not os.path.isdir(new):
            print("нет", new)
            continue
        out[c] = gate_cmp(new, old)
    bad = {c: [f for f, ok in v.items() if not ok] for c, v in out.items()}
    nok = sum(1 for v in out.values() for ok in v.values() if ok)
    ntot = sum(len(v) for v in out.values())
    print("== ворота (v20, опции выкл. — против v18, e16): %d из %d файлов совпали; %s" % (nok, ntot, "ВСЁ СОВПАДАЕТ" if not any(bad.values()) else "РАСХОЖДЕНИЕ " + str({c: v for c, v in bad.items() if v})))
    json.dump(out, open(os.path.join(ROOT, "gate20", "gate-v20.json"), "w"), ensure_ascii=False, indent=1)


def do_grid(R, cells, m_add, mode):
    """cells — список id клеток (grid); в comb — ow_e и comb_<сторона>."""
    fq = fq_days()
    detstat.patch(report_tick)
    out = {"mode": mode}
    ctrl_dir = os.path.join(R, CTRL)
    for side in SIDES:
        m = BASE_M[side] + m_add.get(side, 0)
        z = ND.inv_cdf(1 - 0.05 / (2 * m))
        res = {"m": m, "z_holm": z, "cells": {}, "pairs": {}}
        print("== %s: m=%d, z=%.3f" % (side, m, z))
        ids = [CTRL] + (cells if mode == "grid" else ["comb_" + side])
        P, D, meta = {}, {side: {}}, {}
        for c in ids:
            d = os.path.join(R, c)
            if not os.path.isdir(d):
                continue
            P[(side, c)] = d
            D[side][c] = wr.daily(d, side)
            meta[c] = json.load(open(os.path.join(d, "meta.json")))[side]
        ids = [c for c in ids if (side, c) in P]
        for c in ids:
            hs = report_tick.halves_stats(side, P, D, c, c, z, fq, None)
            ts = tstats(P[(side, c)], side)
            r = {"halves": hs, "verdict": report_tick.verdict(hs, z), "total": float(D[side][c].sum()), "trades": ts,
                 "lot2": meta[c].get("lot2_[now,later,cancelled,rejected]"), "entry_rej": meta[c].get("entry_rej_[beyond,stale]"),
                 "rej": meta[c].get("rej_[consumed,busy,coin_pause,global_pause,cap,no_outcome]"), "signals_total": meta[c].get("signals_total")}
            res["cells"][c] = r
            a, b = hs["A"], hs["B"]
            print("  [%s] %s  A %+.2f [%.2f; %.2f] n=%d | B %+.2f [%.2f; %.2f] n=%d | 60 сут. %+.2f $, сделок %d, сумма %+.2f $, среднее %+.3f $, в плюсе %.1f%% | виды %s%s | lowH A %.2f B %.2f" % (
                c, r["verdict"], a["mean"], a["lo"], a["hi"], a["trades"], b["mean"], b["lo"], b["hi"], b["trades"], r["total"], ts["n"], ts["sum"], ts["mean"],
                ts["pos_share"] * 100, ts["kinds"], (" | lot2[now,later,cancelled,rejected] %s" % (r["lot2"],)) if r["lot2"] is not None else "", a["low_holm"], b["low_holm"]))
            if c == CTRL or mode == "comb":
                for k in ("no_best2_days", "no_best2_coins", "after_first_day", "no_worst2_days", "no_worst2_coins"):
                    print("      %s: A %+.2f [%.2f; %.2f] | B %+.2f [%.2f; %.2f]" % (k, a[k]["mean"], a[k]["lo"], a[k]["hi"], b[k]["mean"], b[k]["lo"], b[k]["hi"]))
        for c in ids:
            if c == CTRL or CTRL not in ids:
                continue
            d = D[side][c] - D[side][CTRL]
            st = {"A": detstat.stat(d[A]), "B": detstat.stat(d[B]), "60": detstat.stat(d)}
            low = st["60"]["mean"] - z * st["60"]["se"]
            rd = wr.read_rule(st["A"], st["B"], st["60"], z)
            tn = {(r["sym"], r["entry_ms"]): float(r["net"]) for r in wr.read_tr(P[(side, c)], side)}
            to = {(r["sym"], r["entry_ms"]): float(r["net"]) for r in wr.read_tr(P[(side, CTRL)], side)}
            gone = [k for k in to if k not in tn]
            new = [k for k in tn if k not in to]
            res["pairs"][c] = {"delta": st, "low_holm_60": low, "read": rd, "gone": {"n": len(gone), "usd": sum(to[k] for k in gone)}, "new": {"n": len(new), "usd": sum(tn[k] for k in new)}}
            print("  Δ(%s − %s): A %+.2f [%.2f; %.2f] | B %+.2f [%.2f; %.2f] | 60 сут. %+.2f [%.2f; %.2f], нижняя после Холма %.2f → %s | исчезло %d (%+.2f $), новых %d (%+.2f $)" % (
                c, CTRL, st["A"]["mean"], st["A"]["lo"], st["A"]["hi"], st["B"]["mean"], st["B"]["lo"], st["B"]["hi"], st["60"]["mean"], st["60"]["lo"], st["60"]["hi"], low, rd,
                len(gone), res["pairs"][c]["gone"]["usd"], len(new), res["pairs"][c]["new"]["usd"]))
        if mode == "grid":
            sel = {}
            for fam, fcells, title in FAMILIES:
                if side not in FAM_SIDES.get(fam, SIDES):
                    continue
                cand = []
                for c in fcells:
                    if c not in res["pairs"]:
                        continue
                    hs = res["cells"][c]["halves"]
                    ok = hs["A"]["trades"] >= MIN_TRADES and hs["B"]["trades"] >= MIN_TRADES
                    cand.append((c, res["pairs"][c]["delta"]["A"]["mean"], res["pairs"][c]["delta"]["B"]["mean"], ok))
                best = max((x for x in cand if x[3]), key=lambda x: x[1], default=None)
                if best is None:
                    sel[fam] = {"cell": None, "why": "нет клетки с ≥ %d сделками в A и в B" % MIN_TRADES, "cands": cand}
                elif best[1] <= 0:
                    sel[fam] = {"cell": None, "why": "нет клетки с Δ_A > 0 (лучшая %s: %+.2f)" % (best[0], best[1]), "cands": cand}
                else:
                    acc = best[2] > 0
                    sel[fam] = {"cell": best[0], "dA": best[1], "dB": best[2], "accepted": acc, "why": "принято (Δ_B > 0)" if acc else "не принято (Δ_B ≤ 0)", "cands": cand}
                s = sel[fam]
                print("  выбор [%s %s]: %s | %s | клетки (Δ_A, Δ_B, маска): %s" % (fam, title, s["cell"], s["why"], "; ".join("%s %+.2f/%+.2f/%s" % (c, a_, b_, "да" if o else "нет") for c, a_, b_, o in cand)))
            res["selection"] = sel
            if sel.get("ls", {}).get("cell") == "ls10":
                print("  >>> ls10 — лучший на A: по §13.19 расширение оси: добавить ls20 (k = 2), +1 к m стороны, повторить")
                res["need_ls20"] = True
        out[side] = res
    return out


def combined_lines(out):
    """Строки вариантов сборки: по стороне отдельная, из принятых значений семейств."""
    lines = []
    for side in SIDES:
        sel = out[side].get("selection", {})
        keys = collections.OrderedDict()
        picked = []
        # es сначала (ключ e, может нести e2), затем e2 — значение семейства e2 главнее
        for fam in ("ls", "tx", "rt", "bm", "es", "e2"):
            s = sel.get(fam)
            if s and s.get("cell") and s.get("accepted", False):
                picked.append(s["cell"])
                keys.update(CELL_KEYS[s["cell"]])
        if not picked:
            lines.append("# %s: приняты 0 семейств — сборки нет (кандидат = ow_e)" % side)
            continue
        lines.append("comb_%s %s %s   # %s" % (side, BASE_LINE, " ".join("%s=%s" % kv for kv in keys.items()), "+".join(picked)))
    return lines


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "grid"
    if mode == "gate":
        do_gate()
        return
    if mode == "grid":
        R = os.path.join(ROOT, "s19")
        cells = [c for _, fc, _ in FAMILIES for c in fc if os.path.isdir(os.path.join(R, c))]
        ext = len(sys.argv) > 2 and sys.argv[2] == "extra"  # ls20 посчитан: +1 к m стороны
        m_add = {s: 1 for s in SIDES} if ext else {}
        out = do_grid(R, cells, m_add, "grid")
        json.dump(out, open(os.path.join(R, "report-s19.json"), "w"), ensure_ascii=False, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
        lines = combined_lines(out)
        print("== сборка (строки вариантов; запись в /data/p42-t566/variants-comb.txt вручную):")
        for ln in lines:
            print("  " + ln)
        open(os.path.join(R, "variants-comb.txt"), "w", encoding="utf-8").write("ow_e %s\n" % BASE_LINE + "\n".join(lines) + "\n")
    elif mode == "comb":
        R = os.path.join(ROOT, "s19c")
        ext = len(sys.argv) > 2 and sys.argv[2] == "extra"
        m_add = {s: 1 for s in SIDES} if ext else {}
        out = do_grid(R, [], m_add, "comb")
        json.dump(out, open(os.path.join(R, "report-s19c.json"), "w"), ensure_ascii=False, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))


if __name__ == "__main__":
    main()
