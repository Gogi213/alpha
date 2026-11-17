"""П-42 §13.18 п. 12 (TK-592): ворота расширенной сетки суток — v20 против v19 на сутках 14…73 (метки и признаки отложенных суток не открываются).

  python3 tk592_hold_gates.py cache <старый кэш> <новый кэш> <выход.json>   — колонки минуток первых 74 суток и строки фандинга совпадают байт в байт
  python3 tk592_hold_gates.py sigsx <старый out/eng> <новый out/eng> <выход.json> — `sigsx` (сигналы суток ≤ 73) и `levels` (уровни, закончившиеся до 74 суток; префикс версий) совпадают
Расхождение — ненулевой код возврата.
"""
from __future__ import annotations

import json
import os
import struct
import sys

import numpy as np
import pandas as pd

OLD_DAYS, NEW_DAYS = 74, 80
OLD_N, NEW_N = OLD_DAYS * 1440, NEW_DAYS * 1440
DAY_MS = 86_400_000
COLS = ["sym", "level", "sig_ms", "dist_e8", "level_px_e8", "born", "end", "first_bar", "natr_e8", "members"]


def read_b42(path, n):
    with open(path, "rb") as f:
        head = f.read(8)
        assert head[:4] == b"B42\x01" and int.from_bytes(head[4:8], "little") == n, (path, head)
        cols = np.fromfile(f, dtype="<i8", count=5 * n).reshape(5, n)
        rest = f.read()
    cnt = struct.unpack("<I", rest[:4])[0]
    fund = rest[4:4 + cnt * 24]
    return cols, cnt, fund


def gate_cache(old, new, out):
    bad, n = [], 0
    for fn in sorted(f for f in os.listdir(old) if f.endswith(".b42")):
        n += 1
        co, fo, ro = read_b42(f"{old}/{fn}", OLD_N)
        cn, fn_, rn = read_b42(f"{new}/{fn}", NEW_N)
        if not np.array_equal(co, cn[:, :OLD_N]) or fo != fn_ or ro != rn:
            bad.append(fn)
    # хвост: суток 74… в новом кэше нет данных до отложенных (14.11 → сутки 74)
    json.dump({"coins": n, "mismatch": bad}, open(out, "w"), ensure_ascii=False)
    print({"coins": n, "mismatch": len(bad)})
    return 1 if bad else 0


def read_sigsx(path):
    return pd.read_csv(path, header=0, names=COLS, dtype={"sym": str, "members": str})


def read_levels(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        head = fh.readline().rstrip("\n").split(",")
        for line in fh:
            rows.append(line.rstrip("\n").split(",", 6))
    return pd.DataFrame(rows, columns=head)


def trunc_segs(s):
    """Версии цены (`минута:цена|…`) только до 74 суток."""
    return "|".join(x for x in s.split("|") if int(x.split(":")[0]) < OLD_N)


def gate_sigsx(old, new, out):
    bad, info = [], {}
    for var in ("b0", "b0p0"):
        for side in ("long", "short"):
            o = read_sigsx(f"{old}/{var}/{side}-sigsx.csv")
            nn = read_sigsx(f"{new}/{var}/{side}-sigsx.csv")
            nn_old = nn[nn["sig_ms"] // DAY_MS <= 73]
            key = ["sym", "level", "sig_ms"]
            a = o.sort_values(key).reset_index(drop=True)
            b = nn_old.sort_values(key).reset_index(drop=True)
            same = len(a) == len(b) and a.equals(b)
            info[f"{var}/{side}/sigsx"] = {"old": len(a), "new_le73": len(b), "new_total": len(nn), "equal": bool(same)}
            if not same:
                bad.append(f"{var}/{side}/sigsx")
            lo = read_levels(f"{old}/{var}/{side}-levels.csv")
            ln_ = read_levels(f"{new}/{var}/{side}-levels.csv")
            m = lo.merge(ln_, on=["sym", "level"], how="outer", suffixes=("_o", "_n"), indicator=True)
            only_old = int((m["_merge"] == "left_only").sum())
            both = m[m["_merge"] == "both"]
            nd = 0
            for r in both.itertuples():
                eo, en = int(r.end_o), int(r.end_n)
                if int(r.born_o) != int(r.born_n) or int(r.first_bar_o) != int(r.first_bar_n):
                    nd += 1
                    continue
                if en < OLD_N:  # уровень закончился до 74 суток — всё совпадает целиком
                    if not (eo == en and r.segs_o == r.segs_n and r.members_o == r.members_n):
                        nd += 1
                elif eo >= OLD_N or eo == 2 ** 32 - 1:  # жив на 13.11 → у нового конец не раньше 74 суток, версии до 74 суток те же
                    if trunc_segs(r.segs_o) != trunc_segs(r.segs_n):
                        nd += 1
                else:
                    nd += 1
            info[f"{var}/{side}/levels"] = {"old": len(lo), "new": len(ln_), "only_old": only_old, "diff": nd}
            if only_old or nd:
                bad.append(f"{var}/{side}/levels")
    json.dump({"info": info, "mismatch": bad}, open(out, "w"), ensure_ascii=False, indent=1)
    print(json.dumps(info, ensure_ascii=False)[:1500], "\nmismatch:", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    mode = sys.argv[1]
    sys.exit(gate_cache(*sys.argv[2:5]) if mode == "cache" else gate_sigsx(*sys.argv[2:5]))
