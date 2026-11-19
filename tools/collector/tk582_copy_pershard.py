#!/usr/bin/env python3
"""TK-582 v3: копия против боевого по видам шардов (book / mark / trade), событие за событием, без суммы по разному числу шардов. Только чтение; запуск под `nice -n 19`.
Боевой — шарды, из которых взят пул копии (`--live-shards`), копия — все её шарды. Печатает на вид: число шардов, события на шард, паузы данных (число ≥ 4 / 20 / 60 с, шард-с, самая длинная) всего и вне окон из `--report-json` (json `tk582_copy_report.py`, поле `windows`).
usage: tk582_copy_pershard.py --live /data/collect/binance/data --copy /data/tk582v3/data --live-shards book00,book01,mark00,trade00,… --from-ms T --to-ms T [--report-json rep24.json] [--json out.json]"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk582_copy_report as R

EVENTS = ("connected", "path_rejected_rtt", "connect_error", "connect_wait", "path_slow_accepted:dark", "path_slow_accepted:sole_source", "path_slow_accepted:uncovered", "path_ok", "path_rejected",
          "ping_degraded:silent_in_gap", "ping_degraded:timeout", "path_switch", "path_upgrade", "storm_hold", "storm_abort", "replace_capped", "path_reject_capped")


def cut(spans, lo, hi):
    return [(max(s, lo), min(e, hi)) for s, e in spans if min(e, hi) > max(s, lo)]


def minus(segs, holes):
    out = segs
    for hs, he in holes:
        out = [x for s, e in out for x in ((s, min(e, hs)), (max(s, he), e)) if x[1] > x[0]]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", required=True)
    ap.add_argument("--copy", required=True)
    ap.add_argument("--live-shards", required=True)
    ap.add_argument("--from-ms", type=int, required=True)
    ap.add_argument("--to-ms", type=int, required=True)
    ap.add_argument("--report-json")
    ap.add_argument("--json")
    a = ap.parse_args()
    lo, hi = a.from_ms, a.to_ms
    holes = sorted((w["lo"], w["hi"]) for w in json.load(open(a.report_json))["windows"]) if a.report_json else []
    sides = {"боевой": (a.live, a.live_shards.split(",")), "копия": (a.copy, R.shards_of(a.copy))}
    res = {}
    for kd in ("book", "mark", "trade"):
        print("## %s" % kd)
        print("| показатель | боевой | копия |\n|---|---|---|")
        rows = collections.defaultdict(dict)
        for nm, (root, shards) in sides.items():
            ks = [s for s in shards if R.kind_of(s) == kd]
            ev = {s: R.read_events(root, s) for s in ks}
            c = collections.Counter()
            for evs in ev.values():
                for e in evs:
                    if lo <= e["ts_ms"] < hi:
                        c[R.key_of(e)] += 1
            rows["шардов"][nm] = len(ks)
            for k in EVENTS:
                if c[k]:
                    rows[k + " (на шард)"][nm] = "%d (%.1f)" % (c[k], c[k] / len(ks))
            for tag, holes_ in (("", []), (" вне окон", holes)):
                tot = 0
                big = collections.Counter()
                longest = 0
                for s, evs in ev.items():
                    for x, y in minus(cut(R.pauses(evs, hi), lo, hi), holes_):
                        d = (y - x) / 1000.0
                        tot += d
                        for th in (4, 20, 60):
                            big[th] += d >= th
                        longest = max(longest, d)
                rows["пауз ≥ 4 / 20 / 60 с%s" % tag][nm] = "%d / %d / %d" % (big[4], big[20], big[60])
                rows["пауза%s, шард-с (на шард)" % tag][nm] = "%.0f (%.0f)" % (tot, tot / len(ks))
                rows["самая длинная пауза%s, с" % tag][nm] = "%.0f" % longest
        for k, v in rows.items():
            print("| %s | %s | %s |" % (k, v.get("боевой", 0), v.get("копия", 0)))
        print()
        res[kd] = {k: v for k, v in rows.items()}
    if a.json:
        json.dump(res, open(a.json, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
