"""Сверка v4 с независимым пересчётом проверяющего (/root/rev534/my_eps.pkl: эпизоды «от нуля», θ = 50, строгие, 22 монеты, по сырью; молчание ног silA / silB по всем записям `b`).

Сравнивается внутри окна проверяющего (t0 = 1794550440000, t1 = 1794849900000), эпизоды ≥ 5 с и ≤ 30 мин: совпадение эпизод в эпизод (монета, биржи, сторона, начало), расхождение длительности, согласие по «нога молчала > 5 с».
Запуск: python3 hold_cmp_review.py <flagged.pkl> <выход.md>
"""
import pickle
import sys

import numpy as np
import pandas as pd

T0, T1 = 1794550440000, 1794849900000


def main(flagp, out):
    tab = pickle.load(open(flagp, "rb"))
    df = tab["df"]
    E = pd.read_pickle("/root/rev534/my_eps.pkl")
    coins = sorted(E["coin"].unique())
    X = df[(df["view"] == "z") & (df["th"] == 50) & df["coin"].isin(coins) & (df["t"] >= T0) & (df["t"] + df["dur"] <= T1) & (df["dur"] >= 5000)]
    R = E[(E["t"] >= T0) & (E["t"] + E["dur"] <= T1) & (E["dur"] >= 5000) & (E["dur"] <= 1_800_000)]
    m = R.merge(X, on=["coin", "exA", "exB", "side", "t"], how="outer", suffixes=("_r", ""), indicator=True)
    both = m[m["_merge"] == "both"]
    L = [f"Окно проверяющего: {pd.to_datetime(T0, unit='ms')} → {pd.to_datetime(T1, unit='ms')} UTC; 22 монеты; «от нуля», θ = 0,5 %, строгий, эпизоды 5 с … 30 мин.", "",
         "| показатель | число |", "|---|---|",
         f"| эпизодов у проверяющего | {len(R)} |", f"| эпизодов в v4 (до фильтров) | {len(X)} |", f"| совпало эпизод в эпизод (монета, биржи, сторона, начало) | {len(both)} |",
         f"| из них длительность сходится до миллисекунды | {int((both['dur_r'] == both['dur']).sum())} |",
         f"| только у проверяющего | {int((m['_merge'] == 'left_only').sum())} |", f"| только в v4 | {int((m['_merge'] == 'right_only').sum())} |"]
    ms = (both["silA"] > 5000) | (both["silB"] > 5000)
    mine = both["f_stale"].astype(bool)
    L += [f"| «молчание ноги > 5 с» по сырью, совпавшие: оба да | {int((ms & mine).sum())} |", f"| оба нет | {int((~ms & ~mine).sum())} |",
          f"| только проверяющий | {int((ms & ~mine).sum())} |", f"| только v4 | {int((~ms & mine).sum())} |"]
    L += ["", f"Доля совпавших по молчанию: {100 * float(((ms == mine)).mean()):.2f} %."]
    open(out, "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
