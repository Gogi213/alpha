#!/usr/bin/env python3
"""TK-500: оркестратор по (актив, сутки) для ядра П-40 (сервер 5.231.25.224, Linux). Один проход сырья бокса -> таблицы эпизодов; `.bin` и сырьё живут только в /dev/shm и удаляются после суток.
Запуск: orch.py <pairs.csv> <out> --days 2026-09-17,2026-09-18 [--jobs 2] [--fetch-p 10] [--shm /dev/shm/p40o] [--assets 1,2]
pairs.csv: asset,pair,exA,symA,multA,lotA,stepA,exB,symB,multB,lotB,stepB   (по строке на пару; ноги с одним (биржа,символ) в актив-сутки читаются и конвертируются ОДИН раз;
  mult — множитель размера Gate (quanto_multiplier), step — шаг количества очереди, пусто = 1 и 1e-9; биржи: binance okx bitget aster hyperliquid bybit gate)
Выход: <out>/<asset>/<day>/<pair>{,.fills.csv,.exits.csv,.features.csv,.path.csv,.aux.csv}, <out>/specs.txt (строки для assemble.py), <out>/orch.csv (замеры по актив-суткам), метка DONE (повтор пропускает).
Флаги ядра P40_* (NOTRADE, THETAS, VARIANTS, TRUNC, K2, SHIFT_B_MS ...) передаются средой как есть. Binance-стакан читается с локального диска (BINANCE_BOOK), остальное — rsync --files-from с бокса в P постоянных соединениях."""
import argparse, concurrent.futures as cf, csv, os, shutil, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
CHFT = {"binance", "okx", "bitget", "aster", "hyperliquid"}
KINDS = ["orderbook", "trades", "mark_price", "open_interest", "liquidations"]
BINANCE_BOOK = os.environ.get("BINANCE_BOOK", "/data/stakan/chft/binance_futures")
CORE = os.environ.get("P40_CORE", "/root/p40core/rs/target/release/p40core")
PQ2BIN = os.environ.get("P40_PQ2BIN", "/root/p40core/pq2bin/target/release/pq2bin")
SSH = "ssh -c aes128-gcm@openssh.com -o Compression=no"
LOG_LOCK = threading.Lock()


def run(cmd, env=None, log=None):
    """cmd -> (rc, wall_s, cpu_s); cpu = user+sys самого процесса и его детей (wait4), не всей программы"""
    t = time.time()
    f = open(log, "ab") if log else subprocess.DEVNULL
    p = subprocess.Popen(cmd, env=env, stdout=f, stderr=subprocess.STDOUT)
    _, st, ru = os.wait4(p.pid, 0)
    p.returncode = os.waitstatus_to_exitcode(st)
    if log:
        f.close()
    return p.returncode, time.time() - t, ru.ru_utime + ru.ru_stime


def leg_key(ex, sym):
    return f"{ex}_{sym}"


def box_files(ex, sym, day):
    """пути на боксе от alpha/stakan/ (то, что нужно ядру и aux_feats)"""
    if ex == "bybit":
        return [f"bybit-linear-ob200/{day}/{sym}_{day}_ob200.data.zip"]
    if ex == "gate":
        d = day.replace("-", "")
        return [f"gate-futures/{sym}/{sym}-{d}{h:02d}.csv.gz" for h in range(24)]
    kinds = KINDS[1:] if ex == "binance" else KINDS
    return [f"chft/{ex}_futures/{day}/{h:02d}/{sym}_{k}.parquet" for h in range(24) for k in kinds]


def fetch(files, raw, p, log):
    os.makedirs(raw, exist_ok=True)
    lst = f"{raw}.list"
    chunks = [files[i::p] for i in range(p)]
    procs = []
    for i, c in enumerate(chunks):
        if not c:
            continue
        open(f"{lst}{i}", "w").write("\n".join(c) + "\n")
        procs.append(subprocess.Popen(["rsync", "-q", "-R", "--ignore-missing-args", f"--files-from={lst}{i}", "-e", SSH, "box:alpha/stakan/", raw + "/"],
                                      stdout=subprocess.DEVNULL, stderr=open(log, "ab")))
    bad = [q.wait() for q in procs]
    for i in range(len(chunks)):
        if os.path.exists(f"{lst}{i}"):
            os.remove(f"{lst}{i}")
    if any(rc not in (0, 23, 24) for rc in bad):
        raise RuntimeError(f"rsync rc {bad}")
    return sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(raw) for f in fs)


def convert(ex, sym, day, raw, bins, mult, log):
    """-> (wall, cpu); bins/A или B: <bins>/B (книга), <bins>/T (сделки; пусто, если сделок нет)"""
    os.makedirs(f"{bins}/T", exist_ok=True)
    w = c = 0.0
    if ex == "bybit":
        z = f"{raw}/bybit-linear-ob200/{day}/{sym}_{day}_ob200.data.zip"
        r = run([PQ2BIN, "bybit", z, f"{bins}/B"], log=log)
    elif ex == "gate":
        r = run([PQ2BIN, "gate", f"{raw}/gate-futures/{sym}", sym, day.replace("-", ""), f"{bins}/B", str(mult), "2"], log=log)
    else:
        root = f"{raw}/chft/{ex}_futures/{day}"
        book_root = f"{BINANCE_BOOK}/{day}" if ex == "binance" else root
        r = run([PQ2BIN, "book", book_root, f"{sym}_orderbook.parquet", f"{bins}/B", "2"], log=log)
        w, c = r[1], r[2]
        if r[0] != 0:
            raise RuntimeError(f"pq2bin book {ex} {sym} rc {r[0]}")
        r2 = run([PQ2BIN, "trades", root, f"{sym}_trades.parquet", f"{bins}/T", "2"], log=log)
        r = (r2[0], w + r2[1], c + r2[2])
    if r[0] != 0:
        raise RuntimeError(f"pq2bin {ex} {sym} rc {r[0]}")
    return r[1], r[2]


def job(args, asset, day, pairs, outlog):
    t_all = time.time()
    out_d = f"{args.out}/{asset}/{day}"
    if os.path.exists(f"{out_d}/DONE"):
        return
    os.makedirs(out_d, exist_ok=True)
    wd = f"{args.shm}/{asset}_{day}"
    shutil.rmtree(wd, ignore_errors=True)
    raw, log = f"{wd}/raw", f"{out_d}/orch.log"
    try:
        legs = {}
        for p in pairs:
            for s in "AB":
                legs.setdefault((p["ex" + s], p["sym" + s]), float(p["mult" + s] or 1))
        files = [f for (ex, sym) in legs for f in box_files(ex, sym, day)]
        t = time.time()
        nbytes = fetch(files, raw, args.fetch_p, log)
        t_fetch = time.time() - t
        t_conv = c_conv = 0.0
        for (ex, sym), mult in legs.items():
            w, c = convert(ex, sym, day, raw, f"{wd}/bin/{leg_key(ex, sym)}", mult, log)
            t_conv, c_conv = t_conv + w, c_conv + c
        t_core = c_core = c_aux = 0.0
        for p in pairs:
            a, b = (f"{wd}/bin/{leg_key(p['ex' + s], p['sym' + s])}" for s in "AB")
            base = f"{out_d}/{p['pair']}"
            cmd = [CORE, base, f"{a}/B", f"{b}/B", f"{a}/T", f"{b}/T", p["lotA"], p["lotB"], str(args.l_ms), p["stepA"] or "1e-9", p["stepB"] or "1e-9"]
            rc, w, c = run(cmd, log=log)
            if rc != 0:
                raise RuntimeError(f"ядро rc {rc} пара {p['pair']}")
            t_core, c_core = t_core + w, c_core + c
            if p["exA"] in CHFT and p["exB"] in CHFT:
                ra, rb = (f"{raw}/chft/{p['ex' + s]}_futures/{day}" for s in "AB")
                rc, w, c = run([sys.executable, f"{HERE}/aux_feats.py", base, ra, p["symA"], rb, p["symB"]], log=log)
                if rc != 0:
                    raise RuntimeError(f"aux rc {rc} пара {p['pair']}")
                c_aux += c
            with LOG_LOCK, open(f"{args.out}/specs.txt", "a") as f:
                byb = int("bybit" in (p["exA"], p["exB"]))
                f.write(f"{asset}:{day.replace('-', '')}:{p['pair']}:{byb}:{base}\n")
        open(f"{out_d}/DONE", "w").write("")
        row = [asset, day, len(pairs), len(legs), nbytes, f"{t_fetch:.1f}", f"{t_conv:.1f}", f"{c_conv:.1f}", f"{t_core:.1f}", f"{c_core:.1f}", f"{c_aux:.1f}", f"{time.time() - t_all:.1f}"]
        with LOG_LOCK, open(outlog, "a") as f:
            f.write(",".join(map(str, row)) + "\n")
        print("готово", *row, flush=True)
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pairs"), ap.add_argument("out")
    ap.add_argument("--days", required=True)
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--fetch-p", type=int, default=10)
    ap.add_argument("--shm", default="/dev/shm/p40o")
    ap.add_argument("--assets", default="")
    ap.add_argument("--l-ms", type=int, default=500)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    outlog = f"{args.out}/orch.csv"
    if not os.path.exists(outlog):
        open(outlog, "w").write("asset,day,pairs,legs,bytes,fetch_s,convert_s,convert_cpu_s,core_s,core_cpu_s,aux_cpu_s,total_s\n")
    by_asset = {}
    for r in csv.DictReader(open(args.pairs)):
        by_asset.setdefault(r["asset"], []).append(r)
    keep = set(filter(None, args.assets.split(",")))
    todo = [(a, d, ps) for d in args.days.split(",") for a, ps in by_asset.items() if not keep or a in keep]
    err = 0
    with cf.ThreadPoolExecutor(args.jobs) as ex:
        futs = {ex.submit(job, args, a, d, ps, outlog): (a, d) for a, d, ps in todo}
        for f in cf.as_completed(futs):
            try:
                f.result()
            except Exception as e:  # noqa: BLE001
                err += 1
                print("СБОЙ", futs[f], e, file=sys.stderr, flush=True)
    sys.exit(1 if err else 0)


main()
