"""П-40 G0: потоковый проход Bybit ob200 (zip на боксе) -> разбор в памяти -> удалить. Для Софии (4 ядра, сырьё только в tmpfs).
Запуск: g0_bybit_stream.py <out.jsonl> <workers> [days_step]. Единица = (сутки, контракт). Возобновляемо; метка <out>.done.
Метрики на файл: сообщения, снимки, немонотонность ts, ts-cts, интервалы между сообщениями, разрывы u (только между двумя
дельтами подряд; снимок обнуляет u), пересечение книги (best bid >= best ask), возраст цены (интервал между сменами лучшей пары)."""
import sys, os, json, subprocess, shutil, zipfile, datetime as dt
from multiprocessing import Pool
import numpy as np
try:
    import orjson; loads = orjson.loads
except ImportError:
    loads = json.loads

BOX = "u677479@u677479.your-storagebox.de"
SSH = ["ssh", "-p", "23", "-i", "/root/.ssh/id_storagebox", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", BOX]
RSH = "ssh -p 23 -i /root/.ssh/id_storagebox -c aes128-gcm@openssh.com -o Compression=no -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
TMP = "/dev/shm/p40"
ROOT = "alpha/stakan/bybit-linear-ob200"

def analyse(path):
    z = zipfile.ZipFile(path); f = z.open(z.namelist()[0])
    ts = []; cts = []; isdelta = []; us = []
    bids = {}; asks = {}; bb = 0.0; ba = float("inf")
    crossed = chk = 0; ages = []; last = None; lbb = lba = None
    for line in f:
        m = loads(line); d = m["data"]; t = m["ts"]
        ts.append(t); cts.append(m.get("cts", 0)); us.append(d.get("u", 0))
        snap = m["type"] == "snapshot"
        isdelta.append(not snap)
        if snap:
            bids.clear(); asks.clear(); bb = 0.0; ba = float("inf")
        for p, q in d["b"]:
            pf = float(p)
            if float(q) == 0:
                if p in bids:
                    del bids[p]
                    if pf == bb: bb = max(map(float, bids)) if bids else 0.0
            else:
                bids[p] = q
                if pf > bb: bb = pf
        for p, q in d["a"]:
            pf = float(p)
            if float(q) == 0:
                if p in asks:
                    del asks[p]
                    if pf == ba: ba = min(map(float, asks)) if asks else float("inf")
            else:
                asks[p] = q
                if pf < ba: ba = pf
        if bids and asks:
            chk += 1
            if bb >= ba: crossed += 1
            if (bb, ba) != (lbb, lba):
                if last is not None: ages.append(t - last)
                last = t; lbb, lba = bb, ba
    ts = np.array(ts); cts = np.array(cts); u = np.array(us); dm = np.array(isdelta)
    pair = dm[1:] & dm[:-1]
    q = lambda a, qs: [float(x) for x in np.percentile(a, qs)] if len(a) else None
    return {"msgs": len(ts), "snapshots": int((~dm).sum()), "ts_nonmono": int((np.diff(ts) < 0).sum()),
            "ts_minus_cts_ms_q": q(ts - cts, [1, 50, 99]), "gap_ms_q": q(np.diff(ts), [50, 90, 99, 99.9]), "gap_max_ms": float(np.diff(ts).max()) if len(ts) > 1 else None,
            "u_gaps": int((np.diff(u)[pair] != 1).sum()), "crossed": crossed, "checked": chk,
            "best_age_ms_q": q(ages, [50, 99, 99.9]), "ts_first": int(ts[0]), "ts_last": int(ts[-1])}

def unit(u):
    day, sym = u
    d = f"{TMP}/{day}_{sym}"; os.makedirs(d, exist_ok=True)
    fn = f"{sym}_{day}_ob200.data.zip"
    r = None
    for attempt in range(3):
        r = subprocess.run(["rsync", "-a", "-e", RSH, f"{BOX}:{ROOT}/{day}/{fn}", d + "/"], capture_output=True, text=True)
        if r.returncode == 0: break
    p = f"{d}/{fn}"
    if not os.path.exists(p):
        shutil.rmtree(d, ignore_errors=True)
        return {"unit": list(u), "missing": True, "rc": r.returncode, "err": r.stderr[-120:]}
    try:
        res = analyse(p); res["bytes"] = os.path.getsize(p)
    except Exception as e:
        res = {"error": repr(e)[:200]}
    res["unit"] = list(u)
    shutil.rmtree(d, ignore_errors=True)
    return res

def list_units(days):
    units = []
    for day in days:
        r = subprocess.run(SSH + [f"ls {ROOT}/{day}"], capture_output=True, text=True)
        for fn in r.stdout.split():
            if fn.endswith("_ob200.data.zip"): units.append((day, fn.split("_")[0]))
    return units

if __name__ == "__main__":
    outp, nw = sys.argv[1], int(sys.argv[2])
    d0 = dt.date(2026, 9, 17)
    days = [(d0 + dt.timedelta(days=i)).isoformat() for i in range(36)]
    if len(sys.argv) > 3: days = days[::int(sys.argv[3])]
    done = set()
    if os.path.exists(outp):
        for l in open(outp):
            try: done.add(tuple(json.loads(l)["unit"]))
            except Exception: pass
    units = [u for u in list_units(days) if u not in done]
    print("units", len(units), flush=True)
    os.makedirs(TMP, exist_ok=True)
    with Pool(nw) as pool, open(outp, "a") as fo:
        for n, res in enumerate(pool.imap_unordered(unit, units, chunksize=1)):
            fo.write(json.dumps(res, ensure_ascii=False) + "\n"); fo.flush()
            if n % 20 == 0: print(n, "/", len(units), flush=True)
    open(outp + ".done", "w").write("ok\n")
