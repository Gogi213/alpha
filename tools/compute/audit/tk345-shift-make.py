#!/usr/bin/env python3
"""TK-345 инвариант (6): проверка заглядывания сдвигом колонок кэша касаний.

    tk345-shift-make.py <src D20/сутки> <dst D20/сутки> <режим X|F|B> <символ> [символ…]

X — исходы (markout/силы/σ-колонки/traded) циклически сдвинуты на строку: результат клетки обязан не измениться
    (движок не читает исходы кэша);
F — признаки касания (возраст, размер, поток, глубина, фронтраннер, ход до касания) взяты у СЛЕДУЮЩЕГО касания
    (утечка из будущего): фильтры по ним обязаны дать другой результат (проверка силы теста);
B — те же признаки взяты у ПРЕДЫДУЩЕГО касания (устаревшие): тоже обязаны измениться.
Строки не переставляются (start_ms/цена/сторона на месте) — сдвигаются только значения перечисленных колонок.
approaches-*.csv и mids1m-*.csv кладутся ссылками.
"""
import csv
import os
import sys

FEAT = ["birth_ms", "age_ms", "size_at_touch", "size_max_before", "flow_1h_lots", "depth_behind_lots",
        "frontrun_lots", "ret_10m_bps", "ret_1h_bps", "ret_4h_bps"]
OUTC = ["m_100ms", "m_1s", "m_10s", "m_60s", "within_touch_100ms", "within_touch_1s", "within_touch_10s",
        "within_touch_60s", "approach_1s", "approach_10s", "m_10m", "m_1h", "m_2h", "strength_held_1s_pct",
        "strength_held_5s_pct", "strength_held_15s_pct", "strength_held_60s_pct", "sigma_60s_bps",
        "sigma_600s_bps", "sigma_3600s_bps", "sigma_7200s_bps", "adverse_60s_bps", "adverse_600s_bps",
        "adverse_3600s_bps", "adverse_7200s_bps", "favour_60s_bps", "favour_600s_bps", "favour_3600s_bps",
        "favour_7200s_bps", "traded_1s", "traded_2s", "traded_3s"]


def main():
    src, dst, mode, syms = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
    os.makedirs(dst, exist_ok=True)
    cols, k = (OUTC, 1) if mode == "X" else (FEAT, 1 if mode == "F" else -1)  # k: у какой строки взять значение
    for s in syms:
        for pre in ("approaches", "mids1m"):
            a = os.path.join(src, f"{pre}-{s}.csv")
            b = os.path.join(dst, f"{pre}-{s}.csv")
            if os.path.exists(a) and not os.path.lexists(b):
                os.symlink(a, b)
        with open(os.path.join(src, f"touches-{s}.csv"), encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))
        head, body = rows[0], rows[1:]
        idx = [head.index(c) for c in cols if c in head]
        n = len(body)
        out = [r[:] for r in body]
        for i, r in enumerate(body):
            j = (i + k) % n
            for c in idx:
                out[i][c] = body[j][c]
        with open(os.path.join(dst, f"touches-{s}.csv"), "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(head)
            w.writerows(out)
        print(mode, s, "строк", n, "колонок", len(idx))


main()
