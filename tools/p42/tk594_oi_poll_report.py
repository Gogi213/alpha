"""П-42 §13.20 п. 2 (TK-594): задержка появления строки `/futures/data/openInterestHist` (period=5m) по журналу `tk594_oi_poll.py`.

Для каждой монеты и отметки `T` — первое время опроса, на котором строка `T` видна (`first_seen`); задержка от конца периода = `first_seen − (T + 5 мин)` (отрицательная — строка видна раньше конца периода:
отметка — момент снимка). Шаг опроса 15 с даёт погрешность до 15 с вверх. Использование: python3 tk594_oi_poll_report.py <oi_poll.jsonl> <выход.json>
"""
import json
import math
import sys
from collections import defaultdict


def main():
    src, out = sys.argv[1], sys.argv[2]
    first = defaultdict(dict)
    last_poll = {}
    errs = 0
    n = 0
    for line in open(src):
        r = json.loads(line)
        n += 1
        if "err" in r:
            errs += 1
            continue
        s = r["sym"]
        last_poll[s] = r["poll_ms"]
        for t in r["ts"]:
            if t not in first[s]:
                first[s][t] = r["poll_ms"]
    # отметки, которые были в самом первом ответе (появились до начала опроса), исключаются: их first_seen — не момент появления
    d_end, d_start = [], []
    per = {}
    for s, m in first.items():
        ts = sorted(m)
        t0 = min(m.values())
        vals = []
        for t in ts:
            if m[t] <= t0 + 1000:  # присутствовали уже в первом опросе
                continue
            vals.append(m[t] - (t + 300_000))
            d_start.append(m[t] - t)
        per[s] = {"rows": len(vals), "mean_s": (sum(vals) / len(vals) / 1000) if vals else None, "max_s": (max(vals) / 1000) if vals else None}
        d_end += vals
    d_end.sort()

    def q(p):
        return d_end[min(len(d_end) - 1, math.ceil(p * len(d_end)) - 1)] / 1000 if d_end else None

    res = {"polls": n, "errors": errs, "rows": len(d_end), "delay_from_period_end_s": {"p50": q(0.5), "p90": q(0.9), "p99": q(0.99), "max": q(1.0)},
           "delay_from_stamp_s": {"p50": sorted(d_start)[len(d_start) // 2] / 1000 if d_start else None}, "per_coin": per}
    json.dump(res, open(out, "w"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != "per_coin"}))


if __name__ == "__main__":
    main()
