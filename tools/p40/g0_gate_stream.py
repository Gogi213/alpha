"""П-40 G0: потоковый проход Gate futures (csv.gz на боксе, часовые файлы) -> разбор -> удалить. Для Софии (сырьё только в tmpfs).
Запуск: g0_gate_stream.py <out.jsonl> <workers> <shard_k> <shard_n>. Единица = (контракт, сутки), 24 часа. Возобновляемо; метка <out>.done.
Формат строки: ts,event,price,size,id,flag; set = снимок на начало часа (size>0 бид, <0 аск), make: L[p]+=size, take: L[p]-=size
(гипотеза B, сверена на AAVE 17.09 03:00: 0 смен знака уровня, 0 пересечений на 210 883 событиях; гипотеза «take: +=» дала 135 смен и 3 207 пересечений).
Метрики на час: строки, события, немонотонность ts, интервалы, смены знака уровня (нарушение B), пересечение best bid >= best ask, возраст цены."""
import sys, os, json, subprocess, shutil, gzip, heapq, datetime as dt
from multiprocessing import Pool
import numpy as np

BOX = "u677479@u677479.your-storagebox.de"
SSH = ["ssh", "-p", "23", "-i", "/root/.ssh/id_storagebox", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", BOX]
RSH = "ssh -p 23 -i /root/.ssh/id_storagebox -c aes128-gcm@openssh.com -o Compression=no -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
TMP = "/dev/shm/p40g"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from g0_gate_fast import hour
ROOT = "alpha/stakan/gate-futures"

def hour_py(path):  # прежняя версия на Python (сверка: байт в байт с g0_gate_fast.hour на AAVE 17.09 03:00)
    L = {}; bh = []; ah = []; flips = crossed = chk = 0; ages = []; last = None; lb = la = None
    ts = []; ev = {"set": 0, "make": 0, "take": 0}
    for line in gzip.open(path, "rt"):
        t, e, p, s, _, _ = line.split(",")
        t = float(t); p = float(p); s = float(s)
        ts.append(t); ev[e] = ev.get(e, 0) + 1
        if e == "set":
            L[p] = s
            if s > 0: heapq.heappush(bh, -p)
            elif s < 0: heapq.heappush(ah, p)
            continue
        old = L.get(p, 0.0)
        new = old + s if e == "make" else old - s
        if old != 0 and new != 0 and (old > 0) != (new > 0): flips += 1
        if abs(new) < 1e-12:
            L.pop(p, None)
        else:
            L[p] = new
            if old == 0 or (old > 0) != (new > 0):
                if new > 0: heapq.heappush(bh, -p)
                else: heapq.heappush(ah, p)
        while bh and L.get(-bh[0], 0.0) <= 0: heapq.heappop(bh)
        while ah and L.get(ah[0], 0.0) >= 0: heapq.heappop(ah)
        if bh and ah:
            chk += 1; bb = -bh[0]; ba = ah[0]
            if bb >= ba: crossed += 1
            if (bb, ba) != (lb, la):
                if last is not None: ages.append(t - last)
                last = t; lb, la = bb, ba
    ts = np.array(ts)
    q = lambda a, qs: [float(x) for x in np.percentile(a, qs)] if len(a) else None
    d = np.diff(ts)
    return {"rows": len(ts), "ev": ev, "ts_nonmono": int((d < 0).sum()), "gap_s_q": q(d, [50, 99, 99.9]), "gap_max_s": float(d.max()) if len(d) else None,
            "flips": flips, "crossed": crossed, "checked": chk, "best_age_s_q": q(ages, [50, 99]), "ts_first": float(ts[0]) if len(ts) else None, "ts_last": float(ts[-1]) if len(ts) else None}

def unit(u):
    c, day = u
    d = f"{TMP}/{c}_{day}"; os.makedirs(d, exist_ok=True)
    pat = f"{c}-{day.replace('-', '')}"
    r = None
    for attempt in range(3):
        r = subprocess.run(["rsync", "-a", "-e", RSH, f"--include={pat}*.csv.gz", "--exclude=*", f"{BOX}:{ROOT}/{c}/", d + "/"], capture_output=True, text=True)
        if r.returncode == 0: break
    files = sorted(os.listdir(d)); hours = {}
    for fn in files:
        h = fn.split(".")[0][-2:]
        try:
            res = hour(f"{d}/{fn}"); res["bytes"] = os.path.getsize(f"{d}/{fn}")
        except Exception as e:
            res = {"error": repr(e)[:200]}
        hours[h] = res
    shutil.rmtree(d, ignore_errors=True)
    return {"unit": list(u), "n_files": len(files), "rc": r.returncode, "hours": hours}

if __name__ == "__main__":
    outp, nw, k, n = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
    r = subprocess.run(SSH + [f"ls {ROOT}"], capture_output=True, text=True)
    cs = sorted(r.stdout.split())[k::n]
    d0 = dt.date(2026, 9, 17); days = [(d0 + dt.timedelta(days=i)).isoformat() for i in range(36)]
    done = set()
    if os.path.exists(outp):
        for l in open(outp):
            try: done.add(tuple(json.loads(l)["unit"]))
            except Exception: pass
    units = [(c, day) for c in cs for day in days if (c, day) not in done]
    print("units", len(units), flush=True)
    os.makedirs(TMP, exist_ok=True)
    with Pool(nw) as pool, open(outp, "a") as fo:
        for i, res in enumerate(pool.imap_unordered(unit, units, chunksize=1)):
            fo.write(json.dumps(res, ensure_ascii=False) + "\n"); fo.flush()
            if i % 10 == 0: print(i, "/", len(units), flush=True)
    open(outp + ".done", "w").write("ok\n")
