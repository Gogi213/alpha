"""П-42 §13.20 (TK-594): пакетная закачка data.binance.vision одним `curl -Z` (HTTP/2, повторное использование соединений) — вместо curl на файл (дорогой TLS на каждый).

Использование (srv): python3 dl_batch.py <виды> <день от> <день до> [параллель=24]   виды: metrics,premium,funding (через запятую)
 metrics → /data/p42-metrics/<SYM>/<SYM>-metrics-<день>.zip; premium → /data/p42-premium/<SYM>/<SYM>-1m-<день>.zip (premiumIndexKlines 1m);
 funding → /data/p42-metrics/funding/<SYM>/<SYM>-fundingRate-<месяц>.zip (месяцы 2026-07…2026-09). Рядом `.sha256` (по `.CHECKSUM` площадки); не-200/sha — `<корень>/log-batch/miss.tsv`.
Существующие файлы пропускаются (докачка идемпотентна).
"""
import datetime as dt
import hashlib
import os
import subprocess
import sys

BASE = "https://data.binance.vision/data/futures/um"
SYMS = [s for s in open("/data/p42-t566/symbols.txt").read().replace(",", "\n").split() if s]
CHUNK = 1500


def tasks(kinds, d0, d1):
    days = []
    d = dt.date.fromisoformat(d0)
    while d <= dt.date.fromisoformat(d1):
        days.append(d.isoformat())
        d += dt.timedelta(days=1)
    for k in kinds:
        if k == "funding":
            for m in ("2026-07", "2026-08", "2026-09"):
                for s in SYMS:
                    yield k, s, m, f"{BASE}/monthly/fundingRate/{s}/{s}-fundingRate-{m}.zip", f"/data/p42-metrics/funding/{s}/{s}-fundingRate-{m}.zip", "/data/p42-metrics"
        else:
            for d in days:
                for s in SYMS:
                    if k == "metrics":
                        yield k, s, d, f"{BASE}/daily/metrics/{s}/{s}-metrics-{d}.zip", f"/data/p42-metrics/{s}/{s}-metrics-{d}.zip", "/data/p42-metrics"
                    else:
                        yield k, s, d, f"{BASE}/daily/premiumIndexKlines/{s}/1m/{s}-1m-{d}.zip", f"/data/p42-premium/{s}/{s}-1m-{d}.zip", "/data/p42-premium"


def run_chunk(chunk, par):
    cfg = []
    for _, _, _, url, dst, _ in chunk:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        cfg += [f'url = "{url}"', f'output = "{dst}.part"', f'url = "{url}.CHECKSUM"', f'output = "{dst}.cs"']
    cf = "/tmp/dl_batch.cfg"
    open(cf, "w").write("\n".join(cfg) + "\n")
    r = subprocess.run(["curl", "-s", "-Z", "--parallel-max", str(par), "--retry", "3", "--retry-all-errors", "-m", "300", "-w", "%{http_code}\t%{url}\n", "-K", cf],
                       capture_output=True, text=True)
    codes = {}
    for line in r.stdout.splitlines():
        if "\t" in line:
            c, u = line.split("\t", 1)
            codes[u] = c
    ok = miss = 0
    for kind, s, d, url, dst, root in chunk:
        c = codes.get(url, "000")
        part, cs = dst + ".part", dst + ".cs"
        good = False
        if c == "200" and os.path.exists(part) and os.path.exists(cs):
            want = open(cs).read().split()[:1]
            got = hashlib.sha256(open(part, "rb").read()).hexdigest()
            if want and want[0] == got:
                os.replace(part, dst)
                open(dst + ".sha256", "w").write(f"{got}  {os.path.basename(dst)}\n")
                good = True
        for f in (part, cs):
            if os.path.exists(f):
                os.remove(f)
        if good:
            ok += 1
        else:
            miss += 1
            os.makedirs(f"{root}/log-batch", exist_ok=True)
            with open(f"{root}/log-batch/miss.tsv", "a") as fh:
                fh.write(f"{kind}\t{s}\t{d}\t{c if c != '200' else 'sha'}\n")
    return ok, miss


def main():
    kinds = sys.argv[1].split(",")
    d0, d1 = sys.argv[2], sys.argv[3]
    par = int(sys.argv[4]) if len(sys.argv) > 4 else 24
    todo = [t for t in tasks(kinds, d0, d1) if not os.path.exists(t[4])]
    print(f"задач {len(todo)}", flush=True)
    tot_ok = tot_miss = 0
    for i in range(0, len(todo), CHUNK):
        ok, miss = run_chunk(todo[i:i + CHUNK], par)
        tot_ok += ok
        tot_miss += miss
        print(f"{i + len(todo[i:i + CHUNK])}/{len(todo)} ok {tot_ok} miss {tot_miss}", flush=True)
    for root in {t[5] for t in todo}:
        os.makedirs(f"{root}/log-batch", exist_ok=True)
        open(f"{root}/log-batch/done", "w").write("1")


if __name__ == "__main__":
    main()
