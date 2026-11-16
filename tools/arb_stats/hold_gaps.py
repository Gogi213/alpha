"""Пропуски сбора по пяти биржам (gaps_scan для EX5) и конец окна: t1 = минимум по биржам последней записи (top-K символов) − 2 мин, округлено до минуты.

Запуск: python3 hold_gaps.py <parsed> <t0_мс> <window.json> <dead.json> [G_с=15]
"""
import glob
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import gaps_scan  # noqa: E402
from hold_common import EX5  # noqa: E402


def main(parsed, t0, win, dead, g_s=15):
    last = {}
    for ex in EX5:
        mx = 0
        for s in gaps_scan.liquid(parsed, ex):
            for f in glob.glob(f"{parsed}/{ex}/{s}.*.npz"):
                mx = max(mx, int(np.load(f)["e"].max()))
        last[ex] = mx
        print(ex, "last book e", gaps_scan.fmt(mx))
    t1 = (min(last.values()) - 120_000) // 60_000 * 60_000
    json.dump({"t0": t0, "t1": t1, "last": last}, open(win, "w"))
    print("window", t0, t1, (t1 - t0) / 3.6e6, "h")
    gaps_scan.EXCHANGES = EX5
    gaps_scan.main(parsed, t0, t1, dead, g_s)


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], int(sys.argv[5]) if len(sys.argv) > 5 else 15)
