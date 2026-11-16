#!/usr/bin/env python3
"""Summary table of prod_hour_profile.py outputs (FINE=1, 1 s windows): per exchange / kind / hour the background lag p50 (median of -15..-1 s
per-second p50), the worst per-second p50 and its second in 0..+5, the max in 0..+5, the largest arrival gap.
Usage: summarize_profiles.py <dir> [<dir> ...]   (dirs hold <exchange>-<trade|book>.txt)"""
import glob, os, re, statistics, sys

ROW = re.compile(r"\s*([+-]\d+)\.\.\s*([+-]\d+) s n=\s*(\d+) lag p50\s+([\d.]+) p99\s+([\d.]+) max\s+([\d.]+)")
GAP = re.compile(r"largest arrival gap in -30\.\.\+90 s: (\d+) ms starting at ([+-][\d.]+) s")


def parse(path):
    hours, cur = {}, None
    for l in open(path):
        m = re.match(r"== hour \d\d-\d\d (\d\d):00", l)
        if m:
            cur = hours[m.group(1)] = {"rows": [], "gap": None}
            continue
        if cur is None:
            continue
        g = GAP.search(l)
        if g:
            cur["gap"] = (int(g.group(1)), float(g.group(2)))
            continue
        r = ROW.match(l)
        if r:
            a, b, n, p50, p99, mx = int(r.group(1)), int(r.group(2)), int(r.group(3)), float(r.group(4)), float(r.group(5)), float(r.group(6))
            cur["rows"].append((a, n, p50, p99, mx))
    return hours


def main():
    out = {}
    for d in sys.argv[1:]:
        for f in sorted(glob.glob(os.path.join(d, "*-*.txt"))):
            ex, kind = os.path.basename(f)[:-4].split("-")
            for h, v in parse(f).items():
                out[(h, ex, kind)] = v
    print("| час UTC | биржа | поток | фон p50 (−15…−1 с) | худшая секунда p50 (с от часа) | max лага в 0…+5 с | n в этой секунде | пауза приёма (мс, с) |")
    print("|---|---|---|---|---|---|---|---|")
    for (h, ex, kind), v in sorted(out.items()):
        rows = v["rows"]
        bg = [p50 for a, n, p50, p99, mx in rows if -15 <= a < 0 and n >= 20]
        win = [r for r in rows if 0 <= r[0] < 6]
        if not bg or not win:
            continue
        worst = max(win, key=lambda r: r[2])
        mx = max(r[4] for r in win)
        gap = f"{v['gap'][0]} @ {v['gap'][1]:+.1f}" if v["gap"] else "—"
        print(f"| {h}:00 | {ex} | {kind} | {statistics.median(bg):.0f} | {worst[2]:.0f} ({worst[0]:+d}) | {mx:.0f} | {worst[1]} | {gap} |")


if __name__ == "__main__":
    main()
