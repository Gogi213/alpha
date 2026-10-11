#!/usr/bin/env python3
"""П-07 H1 (CEO 27.09): пороги p25/p50/p75 `frontrun_lots_at_arm` по сделкам базы Г-85а.

Джойн rounds.csv (p07a-base, все 54 суток) -> approaches-<SYM>.csv по (symbol, day_utc, arm_ms =
t0_ns // 1e6, side=bid) - тот же ключ, что и у остального П-07/Т-32 джойна с кэшем подходов.

    python3 p07-h1-quantiles.py
"""
import csv
import glob
import os
import statistics

A = os.path.expanduser("~/alpha")
HOMES = {
    "aug": (os.path.join(A, "epochs/e-aug"), os.path.join(A, "epochs/e-aug/study/approaches/D20")),
    "hist": (os.path.join(A, "tmp-lsk0914.used-20260926/home"),
             os.path.join(A, "tmp-lsk0914.used-20260926/home/study/approaches/D20")),
    "rec": (os.path.join(A, "tmp-t29/rec"), os.path.join(A, "tmp-t29/rec/study/approaches/D20")),
}
SET_ = "t-bid-btc4h-q1"


def read_body(path):
    with open(path, encoding="utf-8", newline="") as fh:
        lines = fh.read().splitlines(keepends=True)
    return [l for l in lines if not l.startswith("#")]


def main():
    vals = []
    n_rounds = 0
    n_matched = 0
    appr_cache = {}
    for tag, (home, appr_root) in HOMES.items():
        for day_dir in sorted(glob.glob(os.path.join(home, "b5", "p07a-base", "20*"))):
            day = os.path.basename(day_dir)
            fp = os.path.join(day_dir, SET_, "rounds.csv")
            if not os.path.exists(fp):
                continue
            body = read_body(fp)
            if not body:
                continue
            for r in csv.DictReader(body):
                n_rounds += 1
                sym = r["symbol"]
                key = (tag, day, sym)
                if key not in appr_cache:
                    fp2 = os.path.join(appr_root, day, f"approaches-{sym}.csv")
                    idx = {}
                    if os.path.exists(fp2):
                        with open(fp2, encoding="utf-8") as fh:
                            for row in csv.DictReader(fh):
                                if row["side"] != "bid":
                                    continue
                                idx[int(row["arm_ms"])] = row
                    appr_cache[key] = idx
                idx = appr_cache[key]
                arm_ms = int(r["t0_ns"]) // 1_000_000 if "t0_ns" in r and r.get("t0_ns") else None
                if arm_ms is None:
                    continue
                row = idx.get(arm_ms)
                if row is None:
                    for cand in (arm_ms - 1, arm_ms + 1):
                        if cand in idx:
                            row = idx[cand]
                            break
                if row is None:
                    continue
                v = row.get("frontrun_lots_at_arm")
                if v in (None, ""):
                    continue
                vals.append(float(v))
                n_matched += 1
    vals.sort()
    def q(p):
        if not vals:
            return None
        i = max(0, min(len(vals) - 1, int(round(p * (len(vals) - 1)))))
        return vals[i]
    print(f"кругов всего: {n_rounds}, сопоставлено с кэшем: {n_matched}")
    print(f"p25={q(0.25):.1f} p50={q(0.5):.1f} p75={q(0.75):.1f}" if vals else "нет значений")
    print("медиана (statistics):", statistics.median(vals) if vals else None)


if __name__ == "__main__":
    main()
