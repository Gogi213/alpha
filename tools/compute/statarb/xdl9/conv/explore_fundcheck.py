#!/usr/bin/env python3
"""TK-493, П-39, G-X-4: расписание фандинга. На 20 символах каждой биржи НЕЗАВИСИМЫЙ счёт числа выплат в окнах 24 ч по сырью (список меток времени выплат файла, подсчёт циклом) сравнивается с n(h = 1440),
которое берёт ex-ante расписание раннера (conv_explore.fund_ex_arrays + n_payments: последняя выплата <= t и интервал по двум последним). Сравнение - на моментах t каждые 360 минут;
«до смены интервала»: если интервал между выплатами от двух последних <= t до конца окна не менялся, расхождение недопустимо (должно быть 0); случаи со сменой интервала считаются отдельно.
  python explore_fundcheck.py --out ~/xdl9/c2x [--per-exchange 20]
"""
import argparse
import os
import random
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conv_xguard as XG        # noqa: E402
import conv_explore as EX       # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.expanduser("~/xdl9"))
    ap.add_argument("--out", default=XG.c2x_home())
    ap.add_argument("--per-exchange", type=int, default=20)
    a = ap.parse_args(argv)
    XG.configure(script_path=os.path.abspath(__file__), out_root=a.out)
    d0, d1 = EX.D0, EX.D_LAST
    N = (d1 - d0 + 1) * EX.MIN_DAY
    rng = random.Random(37)
    rows, bad_total = [], 0
    for ex in sorted(os.listdir(os.path.join(a.root, "funding"))):
        files = sorted(os.listdir(os.path.join(a.root, "funding", ex)))
        pick = rng.sample(files, min(a.per_exchange, len(files)))
        n_s = n_ok = n_chg = n_bad = 0
        for fn in pick:
            ts, rs, ih = XG.load_funding_full(os.path.join(a.root, "funding", ex, fn), d0, d1)
            if len(ts) < 3:
                continue
            m = (ts - d0 * EX.MIN_DAY).tolist()
            ml, rh, I = EX.fund_ex_arrays(np.array(m, np.int64), rs, ih, N)
            for t in range(600, N - 1500, 360):
                known = [x for x in m if x <= t]
                if not known:
                    continue
                f = t + 2
                got = EX.n_payments(ml[t:t + 1], I[t:t + 1], np.array([f]), 1440)[0][0]
                want = sum(1 for x in m if f + 1 <= x <= f + 1439)                           # независимый счёт по сырью
                diffs = {m[i + 1] - m[i] for i in range(len(m) - 1) if m[i] >= (known[-2] if len(known) >= 2 else known[-1]) and m[i + 1] <= f + 1439}
                stable = len(diffs) <= 1
                n_s += 1
                if got == want:
                    n_ok += 1
                elif stable:
                    n_bad += 1
                else:
                    n_chg += 1
        rows.append([ex, len(pick), n_s, n_ok, n_chg, n_bad])
        bad_total += n_bad
    XG.write_csv(os.path.join(a.out, "tables", "fund_schedule_check.csv"), ["exchange", "symbols", "samples", "match", "mismatch_interval_changed", "mismatch_stable_interval"], rows)
    for r in rows:
        print(r)
    print("G-X-4: расхождений при неизменном интервале: %d" % bad_total)
    return 1 if bad_total else 0


if __name__ == "__main__":
    raise SystemExit(main())
