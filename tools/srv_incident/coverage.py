#!/usr/bin/env python3
"""TK-581: охват данных за окно по сырым файлам коллектора (не по его счётчикам).
Для каждой биржи читает файлы шардов `<шард>-<запуск>.tsv.zst` дня (запуск, в котором лежит начало окна), восстанавливает время приёма (нарастающая сумма `dns`)
и считает:
  1. записи `b` / `t` / `m` по секунде ПРИЁМА (rx) и по секунде БИРЖИ (E = rx_мс − lag) — все шарды вместе;
  1b. по символу и секунде БИРЖИ — число записей (b / t / m, предел 255): «карта охвата» для поиска дыр у ликвидных символов (cov_sym.py);
  2. у бирж с сплошными номерами сделок (Binance, OKX, BingX — столбцы f..l): дыры в номерах по символу в порядке приёма; запись, пришедшая позже и закрывшая дыру,
     дыру снимает; остаток на конец окна = номера, которых нет в записях ws (до дозапроса REST). Пишутся дыры, созданные в окне.
Выход — JSON. Запуск на srv под systemd-run (nice 19, IO idle):
  python3 coverage.py <биржа> <HH:MM:SS> <HH:MM:SS> <out.json> [день=2026-11-16] [процессов=2]
"""
import sys, os, re, glob, json, subprocess, collections, base64, zlib, datetime as dt
from multiprocessing import Pool

ROOTS = {"binance": "/data/collect/binance/data", "bybit": "/data/turbo-probe/soak-bybit/data", "okx": "/data/collect/okx/data",
         "bingx": "/data/collect/bingx/data", "gate": "/data/collect/gate/data"}
SEQ = {"binance", "okx", "bingx"}
MARGIN = 300  # секунд до начала окна, в которые попадает E принятых записей


def pick_files(ex, day, w0_s):
    """по шарду — файл запуска с наибольшим номером запуска <= начала окна."""
    best = {}
    for p in glob.glob(f"{ROOTS[ex]}/{day}/*.tsv.zst"):
        m = re.match(r"(book|trade|mark)(\d+)-(\d+)\.tsv\.zst$", os.path.basename(p))
        if not m:
            continue
        shard, run = m.group(1) + m.group(2), int(m.group(3))
        if run <= w0_s and (shard not in best or run > best[shard][0]):
            best[shard] = (run, p)
    return sorted((s, r, p) for s, (r, p) in best.items())


def scan(args):
    ex, shard, run, path, w0_ms, w1_ms = args
    cnt_rx = collections.defaultdict(lambda: collections.Counter())
    cnt_e = collections.defaultdict(lambda: collections.Counter())
    lags = collections.defaultdict(list)
    holes = {}          # sym -> [[lo, hi, rx_ms_created], ...]
    top = {}            # sym -> наибольший l
    prev_l = {}         # sym -> l предыдущей записи файла (для разностей)
    nrec = 0
    first_rx = None
    nsec = (w1_ms - w0_ms) // 1000 + 2 * MARGIN
    symmap = {"b": {}, "t": {}, "m": {}}
    stats = collections.Counter()
    proc = subprocess.Popen(["zstd", "-dcq", path], stdout=subprocess.PIPE, bufsize=1 << 22)
    t = 0
    w1_ns = w1_ms * 1_000_000
    seq = ex in SEQ
    for line in proc.stdout:
        f = line.split(b"\t", 10)
        try:
            t += int(f[1])
        except (ValueError, IndexError):
            continue
        if first_rx is None:
            first_rx = t
        if t >= w1_ns:
            break
        ty = f[0]
        if ty == b"t":
            if len(f) < 10:
                continue
            sym = f[4]
            try:
                fc = int(f[9]); lf = int(f[8])
            except ValueError:
                continue
            fa = fc if sym not in prev_l else prev_l[sym] + 1 + fc
            la = fa + lf
            prev_l[sym] = la
            ms = t // 1_000_000
            if ms < w0_ms:
                if seq and (sym not in top or la > top[sym]):
                    top[sym] = la
                continue
            if seq:
                tp = top.get(sym)
                if tp is None:
                    top[sym] = la
                else:
                    if fa > tp + 1:
                        holes.setdefault(sym, []).append([tp + 1, fa - 1, ms])
                    elif fa <= tp:
                        stats["t_old_or_dup"] += 1
                        hl = holes.get(sym)
                        if hl:
                            new = []
                            for lo, hi, c in hl:
                                if la < lo or fa > hi:
                                    new.append([lo, hi, c]); continue
                                if fa > lo:
                                    new.append([lo, fa - 1, c])
                                if la < hi:
                                    new.append([la + 1, hi, c])
                            holes[sym] = new
                    if la > tp:
                        top[sym] = la
        elif ty == b"b" or ty == b"m":
            ms = t // 1_000_000
            if ms < w0_ms:
                continue
        else:
            continue
        ms = t // 1_000_000
        try:
            lag = int(f[2])
        except (ValueError, IndexError):
            continue
        k = ty.decode()
        sr = (ms - w0_ms) // 1000
        se = (ms - lag - w0_ms) // 1000
        cnt_rx[k][sr] += 1
        cnt_e[k][se] += 1
        nrec += 1
        j = se + MARGIN
        if 0 <= j < nsec:
            sy = f[3] if ty == b"m" else f[4]
            arr = symmap[k].get(sy)
            if arr is None:
                arr = symmap[k][sy] = bytearray(nsec)
            if arr[j] < 255:
                arr[j] += 1
    proc.kill()
    proc.wait()
    return {"shard": shard, "run": run, "first_rx_ms": (first_rx or 0) // 1_000_000, "last_rx_ms": t // 1_000_000, "n": nrec,
            "rx": {k: dict(v) for k, v in cnt_rx.items()}, "e": {k: dict(v) for k, v in cnt_e.items()},
            "holes": {s.decode(): v for s, v in holes.items() if v}, "stats": dict(stats),
            "sym": {k: {sy.decode(errors="replace"): base64.b64encode(zlib.compress(bytes(a), 6)).decode() for sy, a in v.items()} for k, v in symmap.items()}}


def main():
    ex, a, b, out = sys.argv[1:5]
    day = sys.argv[5] if len(sys.argv) > 5 else "2026-11-16"
    procs = int(sys.argv[6]) if len(sys.argv) > 6 else 2
    d0 = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
    sec = lambda s: int(d0 + int(s[:2]) * 3600 + int(s[3:5]) * 60 + int(s[6:]))
    w0_s, w1_s = sec(a), sec(b)
    files = pick_files(ex, day, w0_s)
    jobs = [(ex, s, r, p, w0_s * 1000, w1_s * 1000) for s, r, p in files]
    print(ex, "files", len(jobs), flush=True)
    with Pool(procs) as pool:
        res = pool.map(scan, jobs, chunksize=1)
    json.dump({"exchange": ex, "w0_ms": w0_s * 1000, "w1_ms": w1_s * 1000, "files": res}, open(out, "w"))
    print("done", ex, sum(r["n"] for r in res), "records", flush=True)


if __name__ == "__main__":
    main()
