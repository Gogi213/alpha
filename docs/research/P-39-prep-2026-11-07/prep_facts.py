#!/usr/bin/env python3
"""П-39 (поиск схождений C2), подготовка: числа протокола, измеряемые по УЖЕ ВИДЕННЫМ сделкам V1/V2 (формирование 2466-2478 + окно 2479-2499).
Только stdlib, ~1 с, ПК не нагружает. Читает docs/research/P-38-window-2026-11-07/{grid_V1,grid_V2}/trades.csv и formation_ref/formation_V*_trades.csv.
Печатает: квантили длительности схождений (CV) на 35 сутках (основание горизонта q90 фильтра фандинга, П-39 §6.B), длительность выходов S1/TO, доли сделок с фандингом,
арифметику границ издержек. Окно уже видено (П-38 §11): числа описательные, пост hoc. Запуск из этой папки:  python prep_facts.py > prep_facts_output.txt
"""
import csv, statistics as st, sys, os

sys.stdout.reconfigure(encoding="utf-8")
W = os.path.join("..", "P-38-window-2026-11-07")
NUM = ("gross", "fee", "fund", "net", "hold", "g_in", "t_sig", "f_in", "f_out", "code")
CODE = {0: "CV", 2: "S1", 3: "S2", 4: "S3", 6: "TO"}


def load(p):
    rs = list(csv.DictReader(open(p, encoding="utf-8")))
    for r in rs:
        for k in NUM:
            r[k] = float(r[k])
    return rs


def q(xs, ps):
    xs = sorted(xs)
    n = len(xs)
    return [xs[min(n - 1, int(p * n))] for p in ps]


for v in ("V1", "V2"):
    F = load(os.path.join(W, "formation_ref", f"formation_{v}_trades.csv"))
    X = load(os.path.join(W, f"grid_{v}", "trades.csv"))
    A = F + X
    cv = [r["hold"] for r in A if r["code"] == 0]
    print(f"[{v}] сделок формирование {len(F)} + окно {len(X)} = {len(A)}; схождений CV {len(cv)}")
    print(f"[{v}] длительность CV, мин, квантили 0,50/0,75/0,90/0,95/0,99:", q(cv, [.5, .75, .9, .95, .99]))
    for nm, S in (("формирование", F), ("окно", X)):
        c = [r["hold"] for r in S if r["code"] == 0]
        print(f"[{v}]   {nm}: CV {len(c)}, квантили 0,50/0,75/0,90/0,95:", q(c, [.5, .75, .9, .95]))
    for code in (2, 6):
        s = [r["hold"] for r in A if r["code"] == code]
        if s:
            print(f"[{v}] выход {CODE[code]}: {len(s)} сделок, длительность q10/q50/q90:", q(s, [.1, .5, .9]))
    nz = [r for r in A if r["fund"] != 0]
    print(f"[{v}] сделок с ненулевым фандингом {len(nz)} из {len(A)} ({len(nz)/len(A):.1%}), средний фандинг по ним {st.mean(r['fund'] for r in nz):.2f} bps")
    print(f"[{v}] окно: gross {st.mean(r['gross'] for r in X):.2f}, фандинг {st.mean(r['fund'] for r in X):.2f}, net {st.mean(r['net'] for r in X):.2f} (комиссия 12,60)")
    base = st.mean(r["net"] for r in X)
    print(f"[{v}] окно, арифметика границ издержек (те же сделки, без модели заполнения): net при 8,82 = {base + 12.6 - 8.82:.2f}; при 5,04 = {base + 12.6 - 5.04:.2f}; безубыточная комиссия (gross+фандинг) = {st.mean(r['gross'] + r['fund'] for r in X):.2f}")
    print(f"[{v}] формирование, безубыточная комиссия (gross+фандинг) = {st.mean(r['gross'] + r['fund'] for r in F):.2f}")
    print(f"[{v}] max g_in {max(r['g_in'] for r in A):.1f} bps")
