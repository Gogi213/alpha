#!/usr/bin/env python3
"""TK-493, П-39 §7, §12.1: стадия 2 и предварительный отбор по ПРАВИЛУ, записанному в протоколе (ручной выбор запрещён; скрипт только исполняет правило над tables/variants_main.csv и variants_extra.csv).

  stage2a  победители семейств A, B, C, E по базам (наибольшая нижняя граница блочного t90 y_d среди вариантов с net на сделку > 0 в ОБЕИХ половинах) -> все непустые подмножества
           размера >= 2 (<= 11 на базу) как строки v_S2a-* (файл stage2a_params.csv для conv_explore run --extra-params ... --stage 2); маски независимы и накладываются вместе;
  stage2b  K = 6 лучших по нижней границе среди прошедших (а)–(г) из стадий 1 и 2a получают прогоны задержки +2 и +5 (строки v_S2b-*, <= 12);
  final    условия (а)–(е) §12.1, порядок по нижней границе, не более N = 3 и не более одного варианта на семейство; окно подтверждения W по MDE (§12.2) -> selection_preview.csv.
Файл `P-39-confirm-selection-<дата>.csv` (коммит ДО скачивания новых суток) этот скрипт НЕ создаёт: preview - расчёт правила; официальный файл и режим подтверждения - отдельный шаг после отчёта поиска.
Слов «подтверждено» нет: прохождение условий отбора - допуск к проверке на новых сутках, не результат.
"""
import argparse
import csv
import itertools
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conv_xguard as XG        # noqa: E402
import conv_explore as EX       # noqa: E402
import explore_stats as ES      # noqa: E402

P = ES.P
FAMS_2A = ("A", "B", "C", "E")
N_PICK, K_2B = 3, 6
TPL = ("id={id}; fam={fam}; base={base}; th={th}; k={k}; p={p}; T={T}; s0={s0}; lam={lam}; ecap={ecap}; q5={q5}; ffilt={ffilt}; sgap={sgap}; excl_ex={excl}; shortban={shortban}; terc={terc}; "
       "out={out}; delay={delay}; cls=crypto; gate=no; stage=2")


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def read_tab(path):
    with open(path, encoding="utf-8", newline="") as f:
        f.readline()
        return {r["id"]: r for r in csv.DictReader(f)}


def spec_fields(v):
    """Вариант (dict из make_variant) -> словарь строковых полей для шаблона TPL."""
    ff = "none" if v["ffilt"] is None else "phi:%s/h:%s" % ("%g" % v["ffilt"][0], v["ffilt"][1])
    sg = "none" if v["sgap"] is None else "n:%d/q:%.2f%s" % (v["sgap"][0], v["sgap"][1], "/u:1" if v["sgap_u"] else "")
    out = "none" if v["out"] is None else (v["out"][0] if v["out"][0] in ("S", "L") else "OX:%.2f" % v["out"][1])
    return dict(base=v["base"], th="%g" % v["th"], k=v["k"], p=v["p"], T=v["T"], s0="none" if v["s0"] is None else "%g" % v["s0"], lam=v["lam"], ecap=v["ecap"], q5="on" if v["q5"] else "off",
                ffilt=ff, sgap=sg, excl=v["excl"] or "none", shortban=v["shortban"] or "none", terc=v["terc"] if v["terc"] else "all", out=out, delay=v["delay"])


def all_variants(a):
    vs = {v["id"]: v for v in EX.parse_variants(os.path.join(a.code_root, EX.PARAMS_REL))}
    suppl = os.path.join(a.code_root, EX.SUPPL_REL)
    if os.path.isfile(suppl):
        vs.update({v["id"]: v for v in EX.parse_variants(suppl)})
    for p in (a.extra_params or "").split(","):
        if p:
            vs.update({v["id"]: v for v in EX.parse_variants(p)})
    return vs


def write_params(path, rows):
    XG.write_csv(path, ["key", "value", "status", "when", "source"], rows)


def cmd_stage2a(a):
    XG.configure(script_path=os.path.abspath(__file__), out_root=a.out)
    vs = all_variants(a)
    tab = read_tab(os.path.join(a.out, "tables", "variants_main.csv"))
    rows, report = [], []
    for base in ("B1", "B2"):
        win = {}
        for fam in FAMS_2A:
            cands = []
            for vid, r in tab.items():
                v = vs.get(vid)
                if v is None or v["fam"] != fam or v["base"] != base or v["univ"] != "main":
                    continue
                lo, n1, n2 = fnum(r["yd_lo_t"]), fnum(r["h1_net"]), fnum(r["h2_net"])
                if lo is None or n1 is None or n2 is None or not (n1 > 0 and n2 > 0) or fnum(r["net"]) is None or not fnum(r["net"]) > 0:
                    continue
                cands.append((lo, vid))
            win[fam] = max(cands)[1] if cands else None
            report.append("%s %s: %s" % (base, fam, win[fam] or "пусто"))
        fams = [f for f in FAMS_2A if win[f]]
        for size in range(2, len(fams) + 1):
            for comb in itertools.combinations(fams, size):
                f = spec_fields(vs["REF-" + base])
                for fam in comb:
                    w = spec_fields(vs[win[fam]])
                    keys = {"A": ("T", "s0", "lam", "ecap"), "B": ("ffilt",), "C": ("sgap",), "E": ("excl", "shortban", "terc", "out")}[fam]
                    for kk in keys:
                        f[kk] = w[kk]
                vid = "S2a-%s-%s" % (base, "".join(comb))
                rows.append(["v_" + vid, TPL.format(id=vid, fam="S2", **f), "правило §7 2a: победители " + ", ".join("%s=%s" % (fm, win[fm]) for fm in comb), "07.11 после стадии 1", "explore_select.py stage2a"])
    write_params(os.path.join(a.out, "tables", "stage2a_params.csv"), rows)
    print("\n".join(report))
    print("строк стадии 2a: %d" % len(rows))


def passes(r, ex, strict_assets=True):
    """Условия (а)–(г) §12.1 по строкам таблиц; -> (прошёл, причины непрохождения)."""
    why = []
    n, n1, n2, assets, dwt = fnum(r["n"]), fnum(r["h1_n"]), fnum(r["h2_n"]), fnum(r["assets"]), fnum(r["days_with_trades"])
    if not (n is not None and n >= 49 and n1 is not None and n1 >= 19 and n2 is not None and n2 >= 30 and assets is not None and assets >= 5 and dwt is not None and dwt >= 12):
        why.append("а")
    nets = [fnum(r["net"]), fnum(r["h1_net"]), fnum(r["h2_net"])]
    if not all(x is not None and x > 0 for x in nets):
        why.append("б")
    lo = fnum(r["yd_lo_t"])
    if not (lo is not None and lo > 0):
        why.append("в")
    w3d, wa = fnum(ex.get("mean_wo_top3days")), fnum(ex.get("yd_wo_top3_assets"))
    if not (w3d is not None and w3d > 0 and wa is not None and wa > 0):
        why.append("г")
    return not why, why


def cmd_stage2b(a):
    XG.configure(script_path=os.path.abspath(__file__), out_root=a.out)
    vs = all_variants(a)
    tab = read_tab(os.path.join(a.out, "tables", "variants_main.csv"))
    with open(os.path.join(a.out, "tables", "variants_extra.csv"), encoding="utf-8", newline="") as f:
        f.readline()
        ext = {r["id"]: r for r in csv.DictReader(f)}
    ok = []
    for vid, r in tab.items():
        v = vs.get(vid)
        if v is None or v["fam"] == "REF" or vid.endswith("-d0") or v["univ"] != "main":
            continue
        good, _ = passes(r, ext.get(vid, {}))
        if good:
            ok.append((fnum(r["yd_lo_t"]), vid))
    ok.sort(reverse=True)
    top = [vid for _, vid in ok[:K_2B]]
    rows = []
    for vid in top:
        for d in (2, 5):
            f = spec_fields(vs[vid])
            f["delay"] = d
            nid = "S2b-%s-d%d" % (vid, d)
            rows.append(["v_" + nid, TPL.format(id=nid, fam="S2", **f), "правило §7 2b: K=6 лучших по нижней границе среди прошедших (а)–(г)", "07.11 после стадии 1/2a", "explore_select.py stage2b"])
    write_params(os.path.join(a.out, "tables", "stage2b_params.csv"), rows)
    print("прошли (а)–(г): %d; взяты %s; строк 2b: %d" % (len(ok), top, len(rows)))


def mde_window(series, m_conf=N_PICK):
    """W ∈ {21, 24, 27}: наименьшее, для которого min(среднее y_d по H1, по H2) не меньше MDE(W) = t(α1, df)·σ_блок/√n_эфф(W), α1 = 0,05/M_подтв (§12.2). -> (W, MDE по W, эффект, σ_блок)."""
    pr, pd = series
    n = len(pr)
    b = P.block_len(n)
    bm = [float(np.mean(pr[i:i + b])) for i in range(0, n, b)]
    sd = float(np.std(bm, ddof=1)) if len(bm) > 1 else float("nan")
    h1 = [x for x, d in zip(pr, pd) if d <= EX.H1_LAST]
    h2 = [x for x, d in zip(pr, pd) if d > EX.H1_LAST]
    eff = min(np.mean(h1), np.mean(h2))
    mdes = {}
    for W_, neff in ((21, 7), (24, 8), (27, 9)):
        mdes[W_] = P._t_ppf_cached(1.0 - (0.05 / m_conf), neff - 1) * sd / math.sqrt(neff)
    pick = next((W_ for W_ in (21, 24, 27) if eff >= mdes[W_]), 27)
    return pick, mdes, float(eff), sd


def cmd_final(a):
    XG.configure(script_path=os.path.abspath(__file__), out_root=a.out)
    vs = all_variants(a)
    tab = read_tab(os.path.join(a.out, "tables", "variants_main.csv"))
    with open(os.path.join(a.out, "tables", "variants_extra.csv"), encoding="utf-8", newline="") as f:
        f.readline()
        ext = {r["id"]: r for r in csv.DictReader(f)}
    data = ES.load_all(a.out, a.tags.split(","), vs)
    rows, picked_fam, picked = [], set(), []
    cand = []
    for vid, r in tab.items():
        v = vs.get(vid)
        if v is None or v["fam"] == "REF" or vid.endswith("-d0") or v["univ"] != "main":
            continue
        good, why = passes(r, ext.get(vid, {}))
        d2 = tab.get("S2b-%s-d2" % vid) if not vid.startswith("S2b-") else None
        n2 = fnum(d2["net"]) if d2 else None
        if good and not vid.startswith("S2b-"):
            if n2 is None or not n2 > 0:
                good, why = False, ["д"]
        rows.append([vid, v["fam"], v["base"], fnum(r["yd_lo_t"]), fnum(r["net"]), fnum(r["h1_net"]), fnum(r["h2_net"]), "да" if good else "нет", "".join(why), n2])
        if good:
            cand.append((fnum(r["yd_lo_t"]), vid))
    cand.sort(reverse=True)
    for lo, vid in cand:
        fam = vs[vid]["fam"]
        if fam in picked_fam or len(picked) >= N_PICK:
            continue
        picked_fam.add(fam)
        picked.append(vid)
    XG.write_csv(os.path.join(a.out, "tables", "selection_all.csv"), ["id", "fam", "base", "yd_lo_t", "net", "h1_net", "h2_net", "допущен_а_д", "не_прошли", "net_при_+2"], rows)
    out = []
    W_pick = None
    for i, vid in enumerate(picked):
        vd = data.get(vid)
        sr = ES.make_series(vd.rows, vd.act)
        W_, mdes, eff, sd = mde_window((sr["primary"], sr["primary_days"]), m_conf=max(len(picked), 1))
        out.append([i + 1, vid, vs[vid]["fam"], tab[vid]["yd_lo_t"], tab[vid]["net"], W_, eff, sd] + [mdes[w] for w in (21, 24, 27)])
        W_pick = max(W_pick or 0, W_)
    XG.write_csv(os.path.join(a.out, "tables", "selection_preview.csv"),
                 ["порядок", "id", "семейство", "нижняя_граница_yd", "net_на_сделку", "W_по_MDE", "эффект_min_H1_H2", "sigma_блок", "MDE_21", "MDE_24", "MDE_27"], out)
    print("допущено к проверке на новых сутках (правило §12.1, preview): %s; W=%s" % (picked or "нет", W_pick))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("stage2a", "stage2b", "final"))
    ap.add_argument("--out", default=XG.c2x_home())
    ap.add_argument("--code-root", default=os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "..")))
    ap.add_argument("--extra-params", default=None, help="stage2a/2b params через запятую (для final: чтобы знать семейства)")
    ap.add_argument("--tags", default="main")
    a = ap.parse_args(argv)
    {"stage2a": cmd_stage2a, "stage2b": cmd_stage2b, "final": cmd_final}[a.cmd](a)


if __name__ == "__main__":
    main()
