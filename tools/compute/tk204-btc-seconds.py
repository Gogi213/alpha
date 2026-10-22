#!/usr/bin/env python3
"""TK-204 / Г-72: лента сделок Bybit BTCUSDT -> close по секундам (цена последней сделки с ts в [s, s+1)).

Договор Судьи (TK-204, 20:18): секунда без сделок — прежний close; до первой сделки суток — close прошлых суток;
86 400 строк/сутки; цена в целых тиках без f64 (не кратна шагу — стоп с выпиской); сверка: close последней секунды
минуты = close 1m (файлы ref-BTCUSDT-1m.csv, иначе REST kline Bybit), расхождения — mismatch.csv; манифест по суткам;
сырьё удаляется после свёртки. Запуск: tk204-btc-seconds.py --from 2026-01-01 --to 2026-10-02 --out /data/tk204/btc-seconds
"""
import argparse, csv, datetime as dt, glob, gzip, hashlib, json, os, sys, time, urllib.request
from multiprocessing import Pool

URL = "https://public.bybit.com/trading/BTCUSDT/BTCUSDT{d}.csv.gz"
KL = "https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=1&start={s}&limit=1000"
TICK_DEN = 10  # шаг 0,1: цена*10 — целое


def ticks(p: bytes) -> int:
    """'68244.50' -> 682445 без f64; дробь длиннее 1 знака с ненулём — ValueError."""
    a, _, b = p.partition(b".")
    b = b.rstrip(b"0")
    if len(b) > 1:
        raise ValueError(p)
    return int(a) * TICK_DEN + (int(b) if b else 0)


def fetch(day: str, raw: str):
    for n in range(5):
        try:
            urllib.request.urlretrieve(URL.format(d=day), raw)
            return
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(10 * (n + 1))
    raise RuntimeError(f"{day}: загрузка: {err}")


def fold(day: str, out: str):
    sparse = f"{out}/work/{day}.sparse.json"
    if os.path.exists(sparse):
        return day
    raw = f"{out}/work/{day}.csv.gz"
    fetch(day, raw)
    base = int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())
    last = {}  # sec -> (ts_int, ticks)
    n = bad = ooo = 0
    first_ts = last_ts = None
    with gzip.open(raw, "rb") as f:
        f.readline()
        for line in f:
            c = line.split(b",", 5)
            ts = c[0]
            a, _, b = ts.partition(b".")
            tsi = int(a) * 10000 + int(b.ljust(4, b"0")[:4]) if b else int(a) * 10000
            try:
                t = ticks(c[4])
            except ValueError:
                bad += 1
                if bad <= 5:
                    print(f"{day}: цена не кратна шагу: {c[4]!r}", flush=True)
                continue
            s = int(a)
            if s < base or s >= base + 86400:
                continue
            n += 1
            if first_ts is None:
                first_ts = tsi
            if last_ts is not None and tsi < last_ts:
                ooo += 1
            last_ts = tsi
            o = last.get(s)
            if o is None or tsi >= o[0]:
                last[s] = (tsi, t)
    if bad:
        raise SystemExit(f"{day}: {bad} цен не кратны шагу — СТОП")
    st = {"day": day, "trades": n, "first_ts": first_ts, "last_ts": last_ts, "out_of_order": ooo,
          "raw_bytes": os.path.getsize(raw), "raw_sha256": hashlib.sha256(open(raw, "rb").read()).hexdigest(),
          "sec": {str(s - base): v[1] for s, v in last.items()}}
    json.dump(st, open(sparse + ".tmp", "w"))
    os.replace(sparse + ".tmp", sparse)
    os.remove(raw)
    return day


def load_ref(globs):
    ref = {}
    for g in globs:
        for p in glob.glob(g):
            for r in csv.reader(open(p)):
                if r and r[0].isdigit():
                    ref[int(r[0])] = r[4]
    return ref


def rest_ref(day: str):
    s = int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()) * 1000
    ref = {}
    for st in (s, s + 1000 * 60000):
        for n in range(6):
            with urllib.request.urlopen(KL.format(s=st), timeout=30) as r:
                j = json.load(r)
            if j.get("retCode") == 0:
                break
            print(f"{day}: kline retCode {j.get('retCode')} {j.get('retMsg')}", flush=True)
            time.sleep(5 * (n + 1))
        else:
            raise RuntimeError(f"{day}: kline: {j}")
        for k in j["result"]["list"]:
            if s <= int(k[0]) < s + 86400000:
                ref[int(k[0])] = k[4]
    return ref


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d0", required=True)
    ap.add_argument("--to", dest="d1", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ref-glob", action="append", default=["/data/alpha/epochs/*/study/regime/ref-BTCUSDT-1m.csv"])
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    os.makedirs(f"{a.out}/work", exist_ok=True)
    d, end, days = dt.date.fromisoformat(a.d0), dt.date.fromisoformat(a.d1), []
    while d <= end:
        days.append(d.isoformat())
        d += dt.timedelta(days=1)
    with Pool(a.workers) as p:
        for x in p.imap_unordered(lambda_fold(a.out), days):
            print("fold", x, flush=True)
    ref_files = load_ref(a.ref_glob)
    man = open(f"{a.out}/manifest.csv", "w", newline="")
    mw = csv.writer(man)
    mw.writerow(["day", "file", "bytes", "sha256", "trades", "first_ts", "last_ts", "empty_secs", "max_pause_s",
                 "minutes_checked", "minutes_mismatch", "ref", "out_of_order", "raw_bytes", "raw_sha256"])
    mm = open(f"{a.out}/mismatch.csv", "w", newline="")
    mmw = csv.writer(mm)
    mmw.writerow(["day", "minute_ms", "sec_close_ticks", "ref_close"])
    prev, unresolved = None, 0
    for day in days:
        st = json.load(open(f"{a.out}/work/{day}.sparse.json"))
        sec = {int(k): v for k, v in st["sec"].items()}
        base_ms = int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()) * 1000
        rows, empty, pause, maxp = [], 0, 0, 0
        for i in range(86400):
            v = sec.get(i)
            if v is None:
                empty += 1
                pause += 1
                maxp = max(maxp, pause)
                v = prev
            else:
                pause = 0
            if v is None:
                unresolved += 1
                v = 0
            prev = v
            rows.append(v)
        fp = f"{a.out}/{day}.csv"
        with open(fp, "w") as f:
            f.write("second_ms,close_ticks\n")
            f.write("".join(f"{base_ms + i * 1000},{v}\n" for i, v in enumerate(rows)))
        ref, src = ref_files, "file"
        if not any((base_ms + 60000 * m) in ref for m in range(1440)):
            ref, src = rest_ref(day), "rest"
        chk = bad = 0
        for m in range(1440):
            r = ref.get(base_ms + m * 60000)
            if r is None:
                continue
            chk += 1
            v = rows[m * 60 + 59]
            if ticks(r.encode()) != v:
                bad += 1
                mmw.writerow([day, base_ms + m * 60000, v, r])
        mw.writerow([day, fp, os.path.getsize(fp), hashlib.sha256(open(fp, "rb").read()).hexdigest(), st["trades"],
                     st["first_ts"], st["last_ts"], empty, maxp, chk, bad, src, st["out_of_order"],
                     st["raw_bytes"], st["raw_sha256"]])
        print("day", day, "chk", chk, "bad", bad, "empty", empty, flush=True)
    man.close(); mm.close()
    if unresolved:
        sys.exit(f"секунд без значения (нет close прошлых суток): {unresolved}")


def lambda_fold(out):
    from functools import partial
    return partial(fold_w, out)


def fold_w(out, day):
    return fold(day, out)


if __name__ == "__main__":
    main()
