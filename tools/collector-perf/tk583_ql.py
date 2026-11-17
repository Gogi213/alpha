#!/usr/bin/env python3
"""TK-583: BingX queue_lag p99 прежнего (29d981c0) и нового (9d5bbf27) бинаря при сопоставимой нагрузке хоста.

Одна метрика — один скрипт по сырью: строки `stats-*.jsonl` (раз в 10 с на шард, `queue_lag_p99_ms`), минута = медиана всех строк вида (book / trade / mark) за минуту;
нагрузка хоста — `sar -u` (sadc раз в 10 с), минута = средний %idle (с nice) и %steal. Контроли — OKX, Bybit, Gate, Binance book (бинарь не менялся в обоих окнах).
Запуск на srv: python3 tk583_ql.py --old-from E --old-to E --new-from E --new-to E   (E — секунды эпохи UTC)
"""
import argparse, glob, json, os, re, statistics, subprocess, time

ROOTS = {
    "bingx": "/data/collect/bingx/data", "okx": "/data/collect/okx/data", "bybit": "/data/turbo-probe/soak-bybit/data",
    "gate": "/data/collect/gate/data", "binance": "/data/collect/binance/data",
}
BINS = [(0, 5), (5, 15), (15, 30), (30, 50), (50, 101)]


def med(v):
    return statistics.median(v) if v else float("nan")


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(q * len(v)))] if v else float("nan")


def minutes(root, lo, hi, kinds):
    """{kind: {minute: [queue_lag_p99_ms...]}} по строкам stats-<kind>NN.jsonl."""
    out = {k: {} for k in kinds}
    for f in glob.glob(f"{root}/stats-*.jsonl"):
        kind = os.path.basename(f)[6:].rstrip("0123456789.jsonl")
        if kind not in out:
            continue
        with open(f) as fh:
            for l in fh:
                if '"ts_ms":' not in l:
                    continue
                try:
                    d = json.loads(l)
                except ValueError:
                    continue
                t = d.get("ts_ms", 0) / 1000.0
                if lo <= t < hi and d.get("queue_lag_p99_ms") is not None:
                    out[kind].setdefault(int(t // 60), []).append(d["queue_lag_p99_ms"])
    return out


def host_minutes(lo, hi):
    """{minute: (idle, steal)} из sar -u по суткам окна."""
    res = {}
    day = lo - lo % 86400
    while day < hi:
        f = "/var/log/sysstat/sa" + time.strftime("%d", time.gmtime(day))
        if os.path.exists(f):
            txt = subprocess.run(["sar", "-u", "-f", f], capture_output=True, text=True, env={"LC_ALL": "C"}).stdout
            acc = {}
            for ln in txt.splitlines():
                m = re.match(r"^(\d\d):(\d\d):(\d\d)\s+all\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)", ln)
                if not m:
                    continue
                t = day + int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
                acc.setdefault(t // 60, []).append((float(m[9]), float(m[8])))
            for k, v in acc.items():
                res[k] = (sum(x[0] for x in v) / len(v), sum(x[1] for x in v) / len(v))
        day += 86400
    return res


def per_min(kmap):
    return {m: med(v) for m, v in kmap.items()}


def main():
    ap = argparse.ArgumentParser()
    for n in ("old-from", "old-to", "new-from", "new-to"):
        ap.add_argument("--" + n, type=int, required=True)
    a = ap.parse_args()
    lo, hi = min(a.old_from, a.new_from), max(a.old_to, a.new_to)
    host = host_minutes(lo, hi)
    data = {}
    for ex, root in ROOTS.items():
        kinds = ("book", "trade", "mark") if ex == "bingx" else ("book",)
        data[ex] = {k: per_min(v) for k, v in minutes(root, lo, hi, kinds).items()}
    windows = {"OLD 29d981c0": (a.old_from, a.old_to), "NEW 9d5bbf27": (a.new_from, a.new_to)}
    print("minute value = median of all 10 s shard rows of the kind in that minute; host idle = sar %idle (incl. nice), mean of the minute\n")
    for kind in ("book", "trade", "mark"):
        print(f"== BingX {kind}: queue_lag p99 (ms) by host idle bin; controls = same minutes, book, median (ms)")
        print(f"{'idle bin':<10}{'window':<14}{'n_min':>6}{'bingx med':>10}{'p90':>8}{'steal':>7}{'okx':>7}{'bybit':>7}{'gate':>7}{'binance':>8}{'bx/ctl':>8}")
        for lo_b, hi_b in BINS:
            for name, (f, t) in windows.items():
                ms = [m for m in range(f // 60 + 2, t // 60) if m in host and lo_b <= host[m][0] < hi_b and m in data["bingx"][kind]]
                if not ms:
                    continue
                bx = [data["bingx"][kind][m] for m in ms]
                ctl = {ex: [data[ex]["book"][m] for m in ms if m in data[ex]["book"]] for ex in ("okx", "bybit", "gate", "binance")}
                cm = [med(v) for v in ctl.values() if v]
                print(f"{lo_b}-{hi_b if hi_b < 101 else 100:<5}{name:<14}{len(ms):>6}{med(bx):>10.1f}{pct(bx, .9):>8.1f}{med([host[m][1] for m in ms]):>7.1f}"
                      + "".join(f"{med(ctl[ex]):>7.1f}" if ex != "binance" else f"{med(ctl[ex]):>8.1f}" for ex in ("okx", "bybit", "gate", "binance"))
                      + f"{med(bx) / med(cm):>8.2f}")
        print()
    print("== whole windows (all minutes after +2 min), BingX book / trade / mark median, controls median, host idle median")
    for name, (f, t) in windows.items():
        ms = list(range(f // 60 + 2, t // 60))
        row = [f"{name}: n {len(ms)}"]
        for kind in ("book", "trade", "mark"):
            v = [data["bingx"][kind][m] for m in ms if m in data["bingx"][kind]]
            row.append(f"bx {kind} {med(v):.1f}/p90 {pct(v, .9):.1f}")
        for ex in ("okx", "bybit", "gate", "binance"):
            v = [data[ex]["book"][m] for m in ms if m in data[ex]["book"]]
            row.append(f"{ex} {med(v):.1f}")
        row.append(f"idle {med([host[m][0] for m in ms if m in host]):.1f}")
        print(" | ".join(row))


if __name__ == "__main__":
    main()
