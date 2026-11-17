"""П-42 §13.14: отчёт по правилу «свежий уровень» (касание в пределах H ч или проторговка K мин). Запуск на srv:

  P42_ROOT=/data/p42-charts/pkroot P42_FQ_COL=first_day_ge_12M python3 fresh_report.py full <544|566>   → таблицы, честный эталон, разность сделок против b0
  P42_ROOT=/data/p42-charts/pkroot P42_FQ_COL=first_day_ge_12M python3 fresh_report.py check            → независимая проверка правила по минуткам, разрез по возрасту касания, S0226 (566)

Прогон — `runfresh.sh` (<U>, имя `fr1`): /data/p42-t<U>/out/fr1/{b0,b0pk,fr_*,fr_*_pk}; для `check` нужны `P42_DETAIL`-файлы b0/b0pk 566 (`/data/p42-t566/out/gate`).
Правила — docs/research/P-42-level-breakout.md §13.14 (записаны до счёта). Холм: m = 676 (лонг) / 665 (шорт).
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import detstat  # noqa: E402
import fresh as fr  # noqa: E402
import report_tick  # noqa: E402
from lib import A, B, HALVES, ND, SIDES, load_all, read_trades  # noqa: E402
from pk_report import read_rule  # noqa: E402
from report import fq_days  # noqa: E402

M = {"long": 676, "short": 665}
MIN_TR = 10
T0 = 1_788_220_800_000


def pm(x):
    return ("%+.2f" % x).replace("-", "−")


def ci(st):
    return "%s [%s; %s]" % (pm(st["mean"]), ("%.2f" % st["lo"]).replace("-", "−"), ("%.2f" % st["hi"]).replace("-", "−"))


def cell_row(side, P, D, v, base, z, fq):
    hs = report_tick.halves_stats(side, P, D, v, v, z, fq, None)
    d = D[side][v] - D[side][base]
    dst = {"A": detstat.stat(d[A]), "B": detstat.stat(d[B]), "60": detstat.stat(d)}
    return {"id": v, "halves": hs, "verdict": report_tick.verdict(hs, z), "total": float(D[side][v].sum()),
            "trades": hs["A"]["trades"] + hs["B"]["trades"], "delta": dst, "delta_low_holm": dst["60"]["mean"] - z * dst["60"]["se"],
            "delta_read": read_rule(None, dst["A"], dst["B"], dst["60"], z)}


def trade_map(P, side, v):
    return {(r["sym"], r["entry_ms"]): float(r["net"]) for r in read_trades(P[(side, v)], side)}


def select(D, T, side, pk, h):
    """Выбор клетки на половине h (§13.14): (имя или 'b0', подробности, принята по «§13.10»)."""
    sl = HALVES[h]
    hi = 0 if h == "A" else 1
    base = fr.base_id(pk)
    pool = [("b0", base)] + [(n, fr.cid(n, pk)) for n in fr.CELLS]
    valid = [(n, v) for n, v in pool if T[side][v][hi] >= MIN_TR]
    info = {"score": {v: float(D[side][v][sl].mean()) for _, v in pool}, "valid": [v for _, v in valid]}
    if not valid:
        info["pick"] = base
        return "b0", info, False
    best = max(info["score"][v] for _, v in valid)
    ties = [n for n, v in valid if abs(info["score"][v] - best) < 1e-12]
    pick = "b0" if "b0" in ties else ties[0]
    info["pick"] = base if pick == "b0" else fr.cid(pick, pk)
    if pick == "b0":
        info["accepted"] = False
        return pick, info, False
    dsel = D[side][info["pick"]][sl] - D[side][base][sl]
    delta = float(dsel.mean())
    se = detstat.stat(dsel)["se"]
    dn = {n: float((D[side][fr.cid(n, pk)][sl] - D[side][base][sl]).mean()) for n in fr.neighbors(pick)}
    plateau = bool(all(v > 0 and v >= delta - se for v in dn.values()))
    info.update(delta=delta, se=se, plateau=plateau, nb_delta=dn)
    ok = delta > 0 and plateau
    info["accepted"] = bool(ok)
    return pick, info, ok


def honest(side, P, D, base, vA, vB, z, fq):
    """Эталон: на A — вариант vA (выбран на B), на B — vB (выбран на A)."""
    hs = report_tick.halves_stats(side, P, D, vA, vB, z, fq, None)
    dA = D[side][vA][A] - D[side][base][A]
    dB = D[side][vB][B] - D[side][base][B]
    dst = {"A": detstat.stat(dA), "B": detstat.stat(dB), "60": detstat.stat(np.concatenate([dA, dB]))}
    return {"vA": vA, "vB": vB, "halves": hs, "verdict": report_tick.verdict(hs, z), "delta": dst,
            "delta_low_holm": dst["60"]["mean"] - z * dst["60"]["se"], "delta_read": read_rule(None, dst["A"], dst["B"], dst["60"], z)}


def cmd_full(U):
    out = "/data/p42-t%s/out" % U
    D, P, T = load_all([os.path.join(out, "fr1")])
    fq = fq_days()
    detstat.patch(report_tick)
    res = {"universe": U, "m": M, "cells": {}, "honest": {}, "select": {}}
    md = []
    for side in SIDES:
        z = ND.inv_cdf(1 - 0.05 / (2 * M[side]))
        print("\n########## вселенная %s, %s: m = %d, z = %.3f" % (U, side, M[side], z))
        for pk in (0, 1):
            base = fr.base_id(pk)
            tag = "%s|pk%d" % (side, pk)
            tb = trade_map(P, side, base)
            rows = {}
            print("\n== %s, pk=%d: клетки" % (side, pk))
            md.append("\n#### %s, `pk=%d`, вселенная %s: клетки\n" % ("лонг" if side == "long" else "шорт", pk, U))
            md.append("| клетка | убрано сделок `b0` | $ убранных (по `b0`) | новых сделок | $ новых | A $/сут [95 %] | сд. A | B $/сут [95 %] | сд. B | итог 60 сут., $ | сделок | §10 | Δ A | Δ B | Δ 60 [95 %] (нижняя после Холма) | чтение |")
            md.append("|---|---:|---:|---:|---:|---|---:|---|---:|---:|---:|---|---|---|---|---|")
            for name in [None] + list(fr.CELLS):
                v = base if name is None else fr.cid(name, pk)
                r = cell_row(side, P, D, v, base, z, fq)
                tc = trade_map(P, side, v)
                removed = [k for k in tb if k not in tc]
                new = [k for k in tc if k not in tb]
                r["removed_n"], r["removed_usd"] = len(removed), float(sum(tb[k] for k in removed))
                r["new_n"], r["new_usd"] = len(new), float(sum(tc[k] for k in new))
                rows[v] = r
                a, b, d = r["halves"]["A"], r["halves"]["B"], r["delta"]
                print("  %-14s убрано %3d (%+.2f $) новых %3d (%+.2f $) | A %s n=%d | B %s n=%d | итог %+.2f $ сд. %d | %s | Δ A %s B %s 60 %s (низ. %.2f) %s" % (
                    v, r["removed_n"], r["removed_usd"], r["new_n"], r["new_usd"], ci(a), a["trades"], ci(b), b["trades"], r["total"], r["trades"], r["verdict"],
                    pm(d["A"]["mean"]), pm(d["B"]["mean"]), ci(d["60"]), r["delta_low_holm"], r["delta_read"]))
                md.append("| `%s` | %d | %s | %d | %s | %s | %d | %s | %d | %s | %d | %s | %s | %s | %s (%s) | %s |" % (
                    v, r["removed_n"], pm(r["removed_usd"]), r["new_n"], pm(r["new_usd"]), ci(a), a["trades"], ci(b), b["trades"], pm(r["total"]), r["trades"], r["verdict"],
                    pm(d["A"]["mean"]), pm(d["B"]["mean"]), ci(d["60"]), pm(r["delta_low_holm"]), r["delta_read"]))
            res["cells"][tag] = rows
            npos = sum(1 for k, r in rows.items() if k != base and r["delta_read"] == "помогает")
            nneg = sum(1 for k, r in rows.items() if k != base and r["delta_read"] == "вредит")
            print("  чтение Δ: «помогает» %d, «вредит» %d, «в шуме» %d из %d" % (npos, nneg, len(fr.CELLS) - npos - nneg, len(fr.CELLS)))
            # честный эталон
            print("== %s, pk=%d: честный эталон (выбор на половине, оценка вне выборки)" % (side, pk))
            md.append("\n#### %s, `pk=%d`, вселенная %s: честный эталон (на A — клетка, выбранная на B; на B — выбранная на A)\n" % ("лонг" if side == "long" else "шорт", pk, U))
            md.append("| чтение | c_A → B | c_B → A | A $/сут [95 %] | сд. A | B $/сут [95 %] | сд. B | §10 | Δ A | Δ B | Δ 60 [95 %] (нижняя после Холма) | чтение Δ |")
            md.append("|---|---|---|---|---:|---|---:|---|---|---|---|---|")
            sel = {h: select(D, T, side, pk, h) for h in ("A", "B")}
            for h in ("A", "B"):
                n, info, ok = sel[h]
                res["select"]["%s|%s" % (tag, h)] = {"pick": info["pick"], "accepted": ok, "delta": info.get("delta"), "plateau": info.get("plateau"),
                                                    "nb_delta": info.get("nb_delta")}
                print("      выбор на %s: %s%s" % (h, info["pick"], "" if n == "b0" else " Δ_%s %+.2f, плато %s, принят по §13.10: %s" % (h, info["delta"], info["plateau"], ok)))
            for reading in ("argmax", "§13.10"):
                nA = sel["A"][0] if reading == "argmax" or sel["A"][2] else "b0"
                nB = sel["B"][0] if reading == "argmax" or sel["B"][2] else "b0"
                idA = base if nA == "b0" else fr.cid(nA, pk)
                idB = base if nB == "b0" else fr.cid(nB, pk)
                r = honest(side, P, D, base, idB, idA, z, fq)
                res["honest"]["%s|%s" % (tag, reading)] = {"cA": idA, "cB": idB, **r}
                a, b, d = r["halves"]["A"], r["halves"]["B"], r["delta"]
                print("  [%s] c_A=%s (на B) c_B=%s (на A): A %s n=%d | B %s n=%d | %s | Δ A %s B %s 60 %s (низ. %.2f) %s" % (
                    reading, idA, idB, ci(a), a["trades"], ci(b), b["trades"], r["verdict"], pm(d["A"]["mean"]), pm(d["B"]["mean"]), ci(d["60"]),
                    r["delta_low_holm"], r["delta_read"]))
                md.append("| %s | `%s` | `%s` | %s | %d | %s | %d | %s | %s | %s | %s (%s) | %s |" % (
                    reading, idA, idB, ci(a), a["trades"], ci(b), b["trades"], r["verdict"], pm(d["A"]["mean"]), pm(d["B"]["mean"]), ci(d["60"]),
                    pm(r["delta_low_holm"]), r["delta_read"]))
    json.dump(res, open(os.path.join(out, "fresh-report-%s.json" % U), "w"), ensure_ascii=False, indent=1,
              default=lambda o: o.item() if hasattr(o, "item") else str(o))
    open(os.path.join(out, "fresh-tables-%s.md" % U), "w", encoding="utf-8").write("\n".join(md) + "\n")


# ───────────────────────── независимая проверка правила по минуткам ─────────────────────────

BUCKETS = [("< 1 ч", 0, 60), ("1–6 ч", 60, 360), ("6–24 ч", 360, 1440), ("1–3 сут", 1440, 4320), ("> 3 сут", 4320, 10 ** 9)]
TAU_PM, KS, BS = 200, (60, 120), (300, 500, 1000)


def int_arrays(a, s):
    """Длинная рама, целые ×1e8: (есть данные, high, close); NaN — дыра."""
    have = ~np.isnan(a[0])
    hi = np.where(have, np.rint((a[1] if s > 0 else -a[2]) * 1e8), 0).astype(np.int64)
    cl = np.where(have, np.rint(s * a[3] * 1e8), 0).astype(np.int64)
    return have, hi, cl


def features_at(have, hi, cl, lp, t, first_bar, tf=60):
    """Возраст последнего касания (мин), число касаний (серий минут), по (K, b): (n_in, beyond) — как правило движка."""
    band = lp - (abs(lp) * TAU_PM) // 100_000
    lo = first_bar * tf
    seg = have[lo:t] & (hi[lo:t] >= band)
    idx = np.flatnonzero(seg)
    age = int(t - (lo + idx[-1])) if idx.size else None
    groups = int(np.sum(seg[1:] & ~seg[:-1]) + (1 if seg.size and seg[0] else 0))
    win = {}
    for k in KS:
        w0 = max(t - k, 0)
        hv, c = have[w0:t], cl[w0:t]
        beyond = bool(np.any(hv & (c >= lp)))
        for b in BS:
            lim = (abs(lp) * b) // 100_000
            n_in = int(np.sum(hv & (c < lp) & ((lp - c) <= lim)))
            win[(k, b)] = (n_in, beyond)
    return age, groups, win


def rule_pass(name, age, win):
    a = fr.CELLS[name]
    if a["mode"] in ("ab", "a") and age is not None and age <= a["H"] * 60:
        return True
    if a["mode"] in ("ab", "b"):
        n_in, beyond = win[(a["K"], int(round(a["b"] * 1000)))]
        if not beyond and n_in >= a["K"] * a["S"] // 100:
            return True
    return False


def cmd_check():
    import charts  # минутки: charts.klines
    from collections import defaultdict

    root = "/data/p42-t566/out"
    D, P, T = load_all([os.path.join(root, "fr1")])
    out = {}
    det = {pk: {s: [json.loads(x) for x in open(os.path.join(root, "gate", fr.base_id(pk), s + "-detail.jsonl"), encoding="utf-8")] for s in SIDES}
           for pk in (0, 1)}
    need = defaultdict(list)
    for pk in (0, 1):
        for s in SIDES:
            for r in det[pk][s]:
                need[r["sym"]].append((s, r))
    featmap = {}
    for sym, items in need.items():
        a = charts.klines(sym)
        arrs = {1: int_arrays(a, 1), -1: int_arrays(a, -1)}
        for s, r in items:
            key = (s, r["sym"], r["entry_ms"])
            if key not in featmap:
                sgn = 1 if s == "long" else -1
                have, hi, cl = arrs[sgn]
                featmap[key] = features_at(have, hi, cl, sgn * r["level_px_e8"], r["sig_min"], r["first_bar"])
    s0226 = None
    for pk in (0, 1):
        base = fr.base_id(pk)
        feats = {s: [featmap[(s, r["sym"], r["entry_ms"])] for r in det[pk][s]] for s in SIDES}
        if pk == 0:
            for r, f in zip(det[0]["short"], feats["short"]):
                if r["sym"] == "MINIMAXUSDT" and abs(r["entry_ms"] - 3_116_287_000) < 2000:
                    s0226 = (r, f)
        # разрез по возрасту последнего касания
        print("\n== разрез сделок %s (566, сигнал-минута `t`, возраст = t − последняя минутка касания): число и $" % base)
        tot = {}
        for s in SIDES:
            for r, f in zip(det[pk][s], feats[s]):
                age = f[0] if f[0] is not None else 10 ** 8
                for nm, lo, hi_ in BUCKETS:
                    if lo <= age < hi_:
                        n, u = tot.get(nm, (0, 0.0))
                        tot[nm] = (n + 1, u + r["net"])
        for nm, _, _ in BUCKETS:
            n, u = tot.get(nm, (0, 0.0))
            print("  %-8s %4d сделок %+8.2f $" % (nm, n, u))
        out["buckets_" + base] = {k: list(v) for k, v in tot.items()}
        # сверка движка с независимой реализацией
        print("== сверка клеток с независимой реализацией (%s)" % base)
        for name in fr.CELLS:
            v = fr.cid(name, pk)
            viol = n_rej = n_abs = n_pass_abs = 0
            for s in SIDES:
                tc = {(r["sym"], int(r["entry_ms"])) for r in read_trades(P[(s, v)], s)}
                for r, f in zip(det[pk][s], feats[s]):
                    ok = rule_pass(name, f[0], f[2])
                    present = (r["sym"], r["entry_ms"]) in tc
                    n_rej += (not ok)
                    n_abs += (not present)
                    if (not ok) and present:
                        viol += 1
                    if ok and not present:
                        n_pass_abs += 1
            print("  %-12s правило отвергло %3d из %d; в клетке отсутствует %3d (из них правило пропускает %3d — слот/пауза); НАРУШЕНИЙ %d" % (
                v, n_rej, len(det[pk]["long"]) + len(det[pk]["short"]), n_abs, n_pass_abs, viol))
            out.setdefault("check", {})[v] = dict(rejected=n_rej, absent=n_abs, absent_but_pass=n_pass_abs, violations=viol)
    if s0226:
        r, f = s0226
        age, groups, win = f
        print("\n== S0226_MINIMAXUSDT (шорт, вход %s ms от 01.09; сигнал-минута %d, уровень %.8f)" % (r["entry_ms"], r["sig_min"], r["level_px_e8"] / 1e8))
        print("  возраст последнего касания: %s мин (%.1f ч); серий касаний в окне от бара первого пивота: %d" % (age, (age or 0) / 60, groups))
        for (k, b), (n_in, beyond) in sorted(win.items()):
            print("  проторговка K=%d b=%.1f %%: минут в полосе %d (нужно K·share: 25 %% → %d, 50 %% → %d, 75 %% → %d), закрытие на уровне/за ним: %s" % (
                k, b / 1000, n_in, k // 4, k // 2, k * 3 // 4, "да" if beyond else "нет"))
        keyid = (r["sym"], r["entry_ms"])
        for pk in (0, 1):
            for name in fr.CELLS:
                v = fr.cid(name, pk)
                present = keyid in {(x["sym"], int(x["entry_ms"])) for x in read_trades(P[("short", v)], "short")}
                print("  %-12s правило %s; в клетке сделка %s" % (v, "пропускает" if rule_pass(name, age, win) else "отвергает", "есть" if present else "нет"))
        out["s0226"] = {"age": age, "groups": groups, "win": {"%d|%d" % k: v for k, v in win.items()}}
    json.dump(out, open(os.path.join(root, "fresh-check.json"), "w"), ensure_ascii=False, indent=1,
              default=lambda o: o.item() if hasattr(o, "item") else str(o))


def cmd_check2():
    """Строгая проверка по детальным файлам клеток (`runfresh.sh 566 fr1d`, `P42_DETAIL=1`): сделка опознаётся по (сторона, монета, время входа, цена уровня).
    (1) каждая сделка клетки проходит независимое правило по своему уровню (ложных допусков 0); (2) каждая сделка `b0`, которую правило отвергает, отсутствует
    в клетке (тот же ключ); пропущенные правилом, но отсутствующие в клетке — слот/пауза."""
    import charts
    from collections import defaultdict

    root = "/data/p42-t566/out"
    ids = {pk: [fr.base_id(pk)] + [fr.cid(n, pk) for n in fr.CELLS] for pk in (0, 1)}
    det = {}
    for pk in (0, 1):
        for v in ids[pk]:
            for s in SIDES:
                det[(v, s)] = [json.loads(x) for x in open(os.path.join(root, "fr1d", v, s + "-detail.jsonl"), encoding="utf-8")]
    need = defaultdict(set)
    for (v, s), rows in det.items():
        for r in rows:
            need[r["sym"]].add((s, r["level_px_e8"], r["sig_min"], r["first_bar"]))
    feat = {}
    for sym, keys in need.items():
        a = charts.klines(sym)
        arrs = {1: int_arrays(a, 1), -1: int_arrays(a, -1)}
        for s, lpx, sm, fb in keys:
            sgn = 1 if s == "long" else -1
            have, hi, cl = arrs[sgn]
            feat[(sym, s, lpx, sm, fb)] = features_at(have, hi, cl, sgn * lpx, sm, fb)
    out = {}
    for pk in (0, 1):
        base = fr.base_id(pk)
        print("\n== строгая проверка (%s): сделки опознаются по (сторона, монета, вход, цена уровня)" % base)
        for name in fr.CELLS:
            v = fr.cid(name, pk)
            false_admit = viol = n_rej = n_abs = n_pass_abs = n_cell = n_base = 0
            for s in SIDES:
                cell = {(r["sym"], r["entry_ms"], r["level_px_e8"]) for r in det[(v, s)]}
                for r in det[(v, s)]:
                    n_cell += 1
                    f = feat[(r["sym"], s, r["level_px_e8"], r["sig_min"], r["first_bar"])]
                    if not rule_pass(name, f[0], f[2]):
                        false_admit += 1
                for r in det[(base, s)]:
                    n_base += 1
                    f = feat[(r["sym"], s, r["level_px_e8"], r["sig_min"], r["first_bar"])]
                    ok = rule_pass(name, f[0], f[2])
                    present = (r["sym"], r["entry_ms"], r["level_px_e8"]) in cell
                    n_rej += (not ok)
                    n_abs += (not present)
                    if (not ok) and present:
                        viol += 1
                    if ok and not present:
                        n_pass_abs += 1
            print("  %-12s сделок клетки %4d, из них не проходят правило (ложный допуск) %d; сделок %s %d: правило отвергло %3d, в клетке отсутствует %3d (правило пропускает %3d — слот/пауза); НАРУШЕНИЙ %d" % (
                v, n_cell, false_admit, base, n_base, n_rej, n_abs, n_pass_abs, viol))
            out[v] = dict(cell_trades=n_cell, false_admits=false_admit, base_trades=n_base, rejected=n_rej, absent=n_abs, absent_but_pass=n_pass_abs, violations=viol)
    json.dump(out, open(os.path.join(root, "fresh-check2.json"), "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    if sys.argv[1] == "full":
        cmd_full(sys.argv[2])
    elif sys.argv[1] == "check2":
        cmd_check2()
    else:
        cmd_check()
