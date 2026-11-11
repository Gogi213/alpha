"""TK-522 (Судья 11.11, п. 3): aux (oi5m, liq5m, Φ) варианта ядра по СОБСТВЕННЫМ t_start варианта.

В d1v4 `<база>.<вариант>.aux.csv` — копия aux реального прохода (orch.py: «aux не зависит от варианта ядра»). У сдвинутого K1 t_start другие,
ключ (theta, t_start) в assemble не находится, строки идут как «неизвестно» (--aux-miss-unknown, -1). Здесь aux считается заново для каждого
`<база>.<вариант>.features.csv`: mark/OI/ликвидации CHFT-ног тянутся с бокса на актив (rsync --files-from, как orch.fetch), aux_feats.py — как в orch.

Запуск: aux_variant.py <out> <pairs.csv> <имя варианта> [--assets 1,2] [--jobs 8] [--conn 8] [--shm /dev/shm/auxv]
Берёт <out>/specs_<имя>.txt (строки asset:day:pair:byb:<база>.<имя>). Прежний файл — `<база>.<имя>.aux.csv.copy522` (один раз); пара с
готовым новым aux (aux.csv новее копии) пропускается — повтор безопасен. Метка <out>/AUXV_<имя>_DONE (только при нуле сбоев).
"""
import argparse, concurrent.futures as cf, csv, datetime, os, re, shutil, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
CHFT = {"binance", "okx", "bitget", "aster", "hyperliquid"}
KINDS_AUX = ["mark_price", "open_interest", "liquidations"]   # orch.KINDS[2:]
SSH = "ssh -c aes128-gcm@openssh.com -o Compression=no"
BOX_RETRY_RC = (12, 255)


def chunk_days(base):
    """`<out>/<актив>/2026-09-17+14/<пара>.<вариант>` -> список суток чанка (имя каталога ГГГГ-ММ-ДД+N = N суток подряд)"""
    m = re.search(r"/(\d{4}-\d{2}-\d{2})\+(\d+)/", base.replace("\\", "/"))
    if not m:
        raise ValueError(f"нет каталога чанка ГГГГ-ММ-ДД+N в {base}")
    d0 = datetime.date.fromisoformat(m.group(1))
    return [(d0 + datetime.timedelta(days=i)).isoformat() for i in range(int(m.group(2)))]


def aux_files(legs, days):
    """пути на боксе от alpha/stakan/: mark/OI/ликвидации CHFT-ног (orch.box_files aux_only, без сделок)"""
    return [f"chft/{ex}_futures/{day}/{h:02d}/{sym}_{k}.parquet" for (ex, sym) in sorted(legs) if ex in CHFT for day in days for h in range(24) for k in KINDS_AUX]


def roots(ex, days, raw):
    return ",".join(f"{raw}/chft/{ex}_futures/{day}" for day in days) if ex in CHFT else "-"


def read_specs(out, vname):
    by_asset = {}
    for ln in open(f"{out}/specs_{vname}.txt"):
        ln = ln.strip()
        if ln:
            asset, _day, pair, _byb, base = ln.split(":", 4)
            by_asset.setdefault(asset, []).append((pair, base))
    return by_asset


def done(base):
    cp, new = f"{base}.aux.csv.copy522", f"{base}.aux.csv"
    return os.path.exists(cp) and os.path.exists(new) and os.path.getmtime(new) > os.path.getmtime(cp)


def box_rsync(cmd, sem, log):
    for k in range(4):
        with sem:
            rc = subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=open(log, "ab"))
        if rc not in BOX_RETRY_RC or k == 3:
            return rc
        time.sleep(5 * (k + 1))


def job(asset, items, pairs, args, sem, fails):
    log = f"{args.out}/auxv_{args.variant}.log"
    todo = [(p, b) for p, b in items if not done(b)]
    if not todo:
        return 0
    days = chunk_days(todo[0][1])
    legs = {(pairs[p]["ex" + s], pairs[p]["sym" + s]) for p, _ in todo for s in "AB"}
    raw = f"{args.shm}/{asset}"
    shutil.rmtree(raw, ignore_errors=True)
    os.makedirs(raw)
    try:
        files = aux_files(legs, days)
        if files:
            lst = f"{raw}.list"
            open(lst, "w").write("\n".join(files) + "\n")
            rc = box_rsync(["rsync", "-q", "-R", "--ignore-missing-args", f"--files-from={lst}", "-e", SSH, "box:alpha/stakan/", raw + "/"], sem, log)
            os.remove(lst)
            if rc not in (0, 23, 24):
                raise RuntimeError(f"rsync rc {rc}")
        for p, base in todo:
            pr = pairs[p]
            cp = f"{base}.aux.csv.copy522"
            if not os.path.exists(cp):
                shutil.copy2(f"{base}.aux.csv", cp)
            rc = subprocess.call([sys.executable, f"{HERE}/aux_feats.py", base, roots(pr["exA"], days, raw), pr["symA"], roots(pr["exB"], days, raw), pr["symB"]],
                                 stdout=subprocess.DEVNULL, stderr=open(log, "ab"))
            if rc != 0:
                raise RuntimeError(f"aux_feats rc {rc} пара {p}")
        return len(todo)
    except Exception as e:   # актив не роняет остальные; итог — код выхода и список в логе
        with open(log, "a") as f:
            f.write(f"СБОЙ актив {asset}: {e}\n")
        fails.append(asset)
        return 0
    finally:
        shutil.rmtree(raw, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out"), ap.add_argument("pairs"), ap.add_argument("variant")
    ap.add_argument("--assets", default="")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--conn", type=int, default=8)
    ap.add_argument("--shm", default="/dev/shm/auxv")
    args = ap.parse_args()
    pairs = {r["pair"]: r for r in csv.DictReader(open(args.pairs))}
    by_asset = read_specs(args.out, args.variant)
    keep = set(filter(None, args.assets.split(",")))
    work = {a: it for a, it in by_asset.items() if not keep or a in keep}
    sem, fails, t0, n = threading.Semaphore(args.conn), [], time.time(), 0
    os.makedirs(args.shm, exist_ok=True)
    with cf.ThreadPoolExecutor(args.jobs) as ex:
        futs = {ex.submit(job, a, it, pairs, args, sem, fails): a for a, it in work.items()}
        for i, f in enumerate(cf.as_completed(futs), 1):
            n += f.result()
            if i % 25 == 0 or i == len(futs):
                print(f"активов {i}/{len(futs)}, пар {n}, сбоев {len(fails)}, {time.time() - t0:.0f} с", flush=True)
    if fails:
        sys.exit(f"сбои: {','.join(sorted(fails))}")
    if not keep:
        open(f"{args.out}/AUXV_{args.variant}_DONE", "w").write(time.strftime("%F %T") + "\n")


if __name__ == "__main__":
    main()
