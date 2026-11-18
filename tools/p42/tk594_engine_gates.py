"""П-42 §13.20 п. 7 (TK-594): ворота движка v20t0.

  python3 tk594_engine_gates.py same  <A out/eng> <B out/eng> <выход.json>
      — (а) прогон без `P42_T0` против v19 (или с явным `P42_T0=2026-09-01` против прогона без него): `sigsx`, `levels` и `*-trades|daily|coinday.csv` байт в байт
        на общих монетах (в файлах сравниваемых прогонов — одни и те же монеты); у v19 против v20t0 — через `--days-le 73` строки суток ≤ 73 (как ворота TK-592).
  python3 tk594_engine_gates.py shift <v19 out/eng> <v20t0 out/eng (P42_T0=2026-07-18)> <выход.json> [--shift-days 45]
      — (б) строки `sigsx` / `levels` v19 после сдвига на 45 суток против v20t0: каждая отличающаяся строка обязана относиться к уровню, у которого бар первого пивота
        или любой пивот-член раньше 01.09 (бар < 1080 при tf = 60); для строки только из v19 — к уровню той же монеты и стороны в v20t0 с таким членом, цена которого
        в момент строки в пределах τ = 0,2 % от цены строки. Любое необъяснённое различие — ненулевой код возврата.
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

DAY_MS = 86_400_000
COLS = ["sym", "level", "sig_ms", "dist_e8", "level_px_e8", "born", "end", "first_bar", "natr_e8", "members"]
U32 = 2 ** 32 - 1
TAU = 0.002


def read_sigsx(path):
    return pd.read_csv(path, header=0, names=COLS, dtype={"sym": str, "members": str})


def read_levels(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        head = fh.readline().rstrip("\n").split(",")
        for line in fh:
            rows.append(line.rstrip("\n").split(",", 6))
    df = pd.DataFrame(rows, columns=head)
    for c in ("level", "born", "end", "first_bar"):
        df[c] = df[c].astype("int64")
    return df


def shift_members(s: str, bars: int) -> str:
    """`бар:цена|…` → бары сдвинуты на `bars`."""
    return "|".join(f"{int(b) + bars}:{p}" for b, p in (x.split(":") for x in s.split("|"))) if s else s


def shift_segs(s: str, minutes: int) -> str:
    return "|".join(f"{int(m) + minutes}:{p}" for m, p in (x.split(":") for x in s.split("|"))) if s else s


def shift_level_members(s: str, bars: int) -> str:
    """Поле `members` файла levels: версии через `|`, члены версии через `,`, член — `бар:цена`."""
    return "|".join(",".join(f"{int(b) + bars}:{p}" for b, p in (m.split(":") for m in v.split(","))) for v in s.split("|")) if s else s


def gate_same(a, b, out):
    bad, n = [], 0
    for var in sorted(d for d in os.listdir(a) if os.path.isdir(f"{a}/{d}") and os.path.isdir(f"{b}/{d}")):
        for fn in sorted(os.listdir(f"{a}/{var}")):
            if not fn.endswith((".csv", ".jsonl")):
                continue
            if not os.path.exists(f"{b}/{var}/{fn}"):
                bad.append(f"{var}/{fn}: нет во втором")
                continue
            n += 1
            if open(f"{a}/{var}/{fn}", "rb").read() != open(f"{b}/{var}/{fn}", "rb").read():
                bad.append(f"{var}/{fn}")
    json.dump({"files": n, "mismatch": bad}, open(out, "w"), ensure_ascii=False, indent=1)
    print({"files": n, "mismatch": bad})
    return 1 if bad else 0


def gate_shift(old, new, out, shift_days=45, horizon_days=80):
    """`end` — как правило TK-592: у v20t0 сетка кончается на сутках `horizon_days` (DAYS_TOTAL = 80), уровень, живший там, имеет u32::MAX,
    а у v19 после сдвига — настоящую минуту ≥ горизонта; такое расхождение `end` не считается различием."""
    hor_min = horizon_days * 1440
    sh_min, sh_bar, sh_ms = shift_days * 1440, shift_days * 24, shift_days * DAY_MS
    pre_bar = sh_bar  # бар 01.09 в сетке v20t0
    res, bad = {}, []
    for var in ("b0", "b0p0"):
        for side in ("long", "short"):
            key = f"{var}/{side}"
            end_rule = 0
            o = read_sigsx(f"{old}/{var}/{side}-sigsx.csv")
            n_ = read_sigsx(f"{new}/{var}/{side}-sigsx.csv")
            lo = read_levels(f"{old}/{var}/{side}-levels.csv")
            ln_ = read_levels(f"{new}/{var}/{side}-levels.csv")
            # окно сравнения: v19 торговые сутки 14…(79 − 45) → в v20t0 сутки 59…79; v20t0 строки с суток ≥ 59 (день сигнала); сетка v20t0 кончается на 80 сутках
            o = o[(o["sig_ms"] // DAY_MS + shift_days) <= 79].copy()
            n_ = n_[(n_["sig_ms"] // DAY_MS) >= 14 + shift_days].copy()
            o["sig_ms"] = o["sig_ms"] + sh_ms
            o["born"] = o["born"] + sh_min
            o["end"] = o["end"].where(o["end"] == U32, o["end"] + sh_min)
            o["first_bar"] = o["first_bar"] + sh_bar
            o["members"] = o["members"].map(lambda s: shift_members(s, sh_bar))
            ln_pre = ln_[(ln_["first_bar"] < pre_bar) | ln_["members"].map(lambda s: any(int(m.split(":")[0]) < pre_bar for v in s.split("|") for m in v.split(",") if m))]
            pre_keys = {(r.sym, int(r.born)) for r in ln_pre.itertuples()}
            k = ["sym", "sig_ms", "level_px_e8"]
            m = o.merge(n_, on=k, how="outer", suffixes=("_o", "_n"), indicator="mrg")
            unexplained, explained, same = 0, 0, 0
            for r in m.itertuples():
                if r.mrg == "both":
                    fields = [("dist_e8",), ("born",), ("end",), ("first_bar",), ("natr_e8",), ("members",)]
                    if r.end_o != r.end_n and r.end_n == U32 and r.end_o >= hor_min:
                        end_rule += 1
                        fields = [f for f in fields if f[0] != "end"]
                    diff = any(getattr(r, f[0] + "_o") != getattr(r, f[0] + "_n") for f in fields)
                    if not diff:
                        same += 1
                        continue
                    born_n = int(r.born_n)
                    if (r.sym, born_n) in pre_keys or int(r.first_bar_n) < pre_bar or any(int(x.split(":")[0]) < pre_bar for x in str(r.members_n).split("|")):
                        explained += 1
                    else:
                        unexplained += 1
                elif r.mrg == "right_only":  # только в v20t0: объяснимо, если уровень с пивотом до 01.09 (в версии v20t0)
                    if (r.sym, int(r.born_n)) in pre_keys or int(r.first_bar_n) < pre_bar or any(int(x.split(":")[0]) < pre_bar for x in str(r.members_n).split("|")):
                        explained += 1
                    else:
                        unexplained += 1
                else:  # только в v19: нужен уровень той же монеты в v20t0 с «старым» членом, цена которого близка к цене строки в этот момент
                    t_min = int(r.sig_ms) // 60_000
                    cand = ln_pre[ln_pre["sym"] == r.sym]
                    ok = False
                    for c in cand.itertuples():
                        if not (int(c.born) <= t_min < (int(c.end) if int(c.end) != U32 else 10 ** 9)):
                            continue
                        segs = [(int(a), int(b)) for a, b in (x.split(":") for x in c.segs.split("|"))]
                        px = None
                        for mm, p_ in segs:
                            if mm <= t_min:
                                px = p_
                        if px is not None and abs(px - abs(int(r.level_px_e8))) <= TAU * abs(int(r.level_px_e8)):
                            ok = True
                            break
                    explained += int(ok)
                    unexplained += int(not ok)
            res[key] = {"old_rows": len(o), "new_rows": len(n_), "same": same, "explained": explained, "unexplained": unexplained,
                        "end_rule": end_rule, "levels_new_pre": len(ln_pre), "levels_new": len(ln_), "levels_old": len(lo)}
            if unexplained:
                bad.append(key)
    json.dump({"info": res, "unexplained": bad}, open(out, "w"), ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False), "\nunexplained:", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "same":
        sys.exit(gate_same(*sys.argv[2:5]))
    if mode == "shift":
        sd = int(sys.argv[6]) if len(sys.argv) > 6 else 45
        sys.exit(gate_shift(*sys.argv[2:5], shift_days=sd))
    sys.exit("режим: same | shift")
