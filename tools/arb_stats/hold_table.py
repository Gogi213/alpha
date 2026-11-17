"""Таблица эпизодов arb-holding из сводки hold_scan: одна строка на эпизод (пара × θ × вид), столбцы эпизода, пары и круга по 9 настройкам исполнения.

Запуск: python3 hold_table.py <scan.pkl> [<scan2.pkl> …] <выход.pkl>  — склеивает несколько сводок (пары не пересекаются) и пишет {"df": DataFrame, "pairs": {...}, "skipped", "days", "t0", "t1"}.
"""
import pickle
import sys

import numpy as np
import pandas as pd

import hold_scan as hs


def build(scans):
    rows, pairs, skipped = [], {}, []
    t0, t1, days = None, None, None
    for sc in scans:
        t0, t1, days = sc["t0"], sc["t1"], sc["days"]
        skipped += sc["skipped"]
        for (coin, a, b), r in sc["pairs"].items():
            pairs[(coin, a, b)] = {k: v for k, v in r.items() if k not in ("eps", "sim")}
            for (th, view), eps in r["eps"].items():
                if not len(eps):
                    continue
                df = pd.DataFrame(eps, columns=hs.COLS)
                df.insert(0, "view", view)
                df.insert(0, "th", th)
                df.insert(0, "exB", b)
                df.insert(0, "exA", a)
                df.insert(0, "coin", coin)
                for (d, kind), arr in r["sim"].get((th, view), {}).items():
                    for ci, c in enumerate(hs.SIM_COLS):
                        df[f"{kind}_{d // 1000}_{c}"] = arr[:, ci]
                rows.append(df)
    df = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if len(df):
        df["day"] = np.searchsorted(np.array(days[1:-1]), df["t"].to_numpy(), "right")
        for c in ("side", "open"):
            df[c] = df[c].astype(int)
    return {"df": df, "pairs": pairs, "skipped": skipped, "days": days, "t0": t0, "t1": t1}


if __name__ == "__main__":
    scans = [pickle.load(open(p, "rb")) for p in sys.argv[1:-1]]
    out = build(scans)
    pickle.dump(out, open(sys.argv[-1], "wb"), protocol=4)
    print({"episodes": len(out["df"]), "pairs": len(out["pairs"]), "skipped": len(out["skipped"])})
