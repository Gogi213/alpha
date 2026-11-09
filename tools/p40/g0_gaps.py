"""П-40 G0 (TK-496 возврат 3, п.1): наибольший промежуток без событий книги внутри часа — для Binance/OKX/Bitget/Aster (CHFT parquet).
Промежуток считается от начала часа (UTC) до первого события, между событиями (event_time, мс) и от последнего события до конца часа.
Binance — локально (/data/stakan/chft/binance_futures/<сутки>/<час>/), остальные — потоком с бокса (rsync часа -> чтение колонки event_time -> удалить).
Запуск: g0_gaps.py <out.jsonl> <workers> <ex,ex,...>. Возобновляемо; метка <out>.done. Исходов не касается."""
import sys, os, json, subprocess, shutil, glob, datetime as dt
from multiprocessing import Pool
import numpy as np, pyarrow.parquet as pq

BOX = "u677479@u677479.your-storagebox.de:alpha/stakan/chft"
SSH = "ssh -p 23 -i /root/.ssh/id_box -c aes128-gcm@openssh.com -o Compression=no -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
LOCAL = {"binance_futures": "/data/stakan/chft/binance_futures"}
TMP = "/root/p40/gaps_tmp"

def hour_ms(day, hh):
    return int(dt.datetime.fromisoformat(day).replace(hour=int(hh), tzinfo=dt.timezone.utc).timestamp() * 1000)

def gap_file(path, t0):
    t = pq.read_table(path, columns=["event_time"])
    ev = np.sort(t["event_time"].to_numpy().astype(np.int64))
    if not len(ev): return {"rows": 0, "gap_max_s": 3600.0}
    inner = float(np.diff(ev).max()) if len(ev) > 1 else 0.0
    first = float(ev[0] - t0); last = float(t0 + 3600000 - ev[-1])
    return {"rows": int(len(ev)), "gap_max_s": max(inner, first, last) / 1000, "inner_s": inner / 1000, "first_s": first / 1000, "last_s": last / 1000}

def unit(u):
    ex, day, hh = u; t0 = hour_ms(day, hh)
    if ex in LOCAL:
        d = f"{LOCAL[ex]}/{day}/{hh}"; files = sorted(glob.glob(d + "/*_orderbook.parquet")); tmp = None; rc = 0
    else:
        tmp = f"{TMP}/{ex}/{day}_{hh}"; os.makedirs(tmp, exist_ok=True)
        for _ in range(3):
            r = subprocess.run(["rsync", "-a", "-e", SSH, "--include=*_orderbook.parquet", "--exclude=*", f"{BOX}/{ex}/{day}/{hh}/", tmp + "/"], capture_output=True, text=True)
            if r.returncode == 0: break
            if "No such file" in r.stderr or r.returncode == 23 and "link_stat" in r.stderr: break
        rc = r.returncode; files = sorted(glob.glob(tmp + "/*_orderbook.parquet"))
    if not files:
        if tmp: shutil.rmtree(tmp, ignore_errors=True)
        return [{"unit": list(u), "missing": True, "rc": rc}]
    out = []
    for f in files:
        try: res = gap_file(f, t0)
        except Exception as e: res = {"error": repr(e)[:120]}
        res["unit"] = list(u); res["file"] = os.path.basename(f); out.append(res)
    if tmp: shutil.rmtree(tmp, ignore_errors=True)
    return out

if __name__ == "__main__":
    outp, nw, exs = sys.argv[1], int(sys.argv[2]), sys.argv[3].split(",")
    done = set()
    if os.path.exists(outp):
        for l in open(outp):
            try: done.add(tuple(json.loads(l)["unit"]))
            except Exception: pass
    d0 = dt.date(2026, 9, 17); units = []
    for i in range(36):
        day = (d0 + dt.timedelta(days=i)).isoformat()
        for ex in exs:
            for h in range(24):
                u = (ex, day, f"{h:02d}")
                if u not in done: units.append(u)
    print(len(units), "единиц", flush=True)
    os.makedirs(TMP, exist_ok=True)
    with Pool(nw) as pool, open(outp, "a") as fo:
        for n, res in enumerate(pool.imap_unordered(unit, units, chunksize=1)):
            for r in res: fo.write(json.dumps(r, ensure_ascii=False) + "\n")
            fo.flush()
            if n % 50 == 0: print(n, "/", len(units), flush=True)
    open(outp + ".done", "w").write("ok\n")
