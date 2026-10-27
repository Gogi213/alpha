#!/usr/bin/env python3
"""TK-420 (копия TK-417, август 2026-08) как отдаёт биржа: linear — дневные public.bybit.com/trading/СИМ/СИМ<день>.csv.gz, spot — месячный
public.bybit.com/spot/СИМ/СИМ-2026-08.csv.gz. Калибровка потоков (В-178) на настоящих файлах, потолок 4 потока (бан 23.10 при 32, Судья 26.10: ≤4):
колено = наименьшая ступень с ≥90 % лучшей скорости при нуле ошибок. Первый 429/403 — потоки вдвое и пауза 60 с. 404 — не ошибка. Перезапуск пропускает готовое.
Использование: tk417-bybit-trades-dl.py <список ex<TAB>mk<TAB>sym> <каталог выхода> [--steps 1,2,4] [--step-s 30]
Выход: <каталог>/bybit-linear|bybit-spot/<СИМ>/<файл>; ход — <каталог>/_calib.tsv, _progress.log, _missing.tsv, _fails.tsv.
"""
import http.client
import os
import ssl
import sys
import threading
import time
from urllib.parse import urlsplit

src = "bybit"
listf, outd = sys.argv[1:3]
steps = [1, 2, 4]
step_s = 30
if "--steps" in sys.argv:
    steps = [int(x) for x in sys.argv[sys.argv.index("--steps") + 1].split(",")]
if "--step-s" in sys.argv:
    step_s = int(sys.argv[sys.argv.index("--step-s") + 1])
os.makedirs(outd, exist_ok=True)
MKDIR = {"spot": "bybit-spot", "linear": "bybit-linear"}
HOST = "public.bybit.com"

inst = []
for ln in open(listf, encoding="utf-8"):
    f = ln.rstrip("\n").split("\t")
    if len(f) == 3 and f[0] == src:
        inst.append((f[1], f[2]))


def items():
    for mk, s in inst:
        if mk == "linear":
            for d in range(1, 32):
                t = "2026-08-%02d" % d
                yield "bybit linear %s %s" % (s, t), "/trading/%s/%s%s.csv.gz" % (s, s, t), os.path.join(outd, MKDIR[mk], s, "%s%s.csv.gz" % (s, t))
        else:
            yield "bybit spot %s 2026-08" % s, "/spot/%s/%s-2026-08.csv.gz" % (s, s), os.path.join(outd, MKDIR[mk], s, "%s-2026-08.csv.gz" % s)


tasks = list(items())
print("задач: %d, инструментов %d" % (len(tasks), len(inst)), flush=True)

lock = threading.Lock()
qi = 0
active_limit = steps[0]
pause_until = 0.0
st = dict(bytes=0, ok=0, miss=0, err=0, hard=0, skip=0)
stop = False
logf = open(os.path.join(outd, "_progress.log"), "a", buffering=1)
missf = open(os.path.join(outd, "_missing.tsv"), "a", buffering=1)
failf = open(os.path.join(outd, "_fails.tsv"), "a", buffering=1)
ctx = ssl.create_default_context()


def log(m):
    logf.write("%s %s\n" % (time.strftime("%m-%d %H:%M:%S"), m))


def next_task():
    global qi
    with lock:
        if qi >= len(tasks):
            return None
        t = tasks[qi]
        qi += 1
        return t


def fetch(conn, path, dest):
    """-> (code, bytes). Исключения — сеть."""
    conn.request("GET", path, headers={"Accept-Encoding": "identity", "User-Agent": "Mozilla/5.0"})
    r = conn.getresponse()
    if r.status != 200:
        r.read()
        return r.status, 0
    cl = r.getheader("Content-Length")
    tmp = dest + ".part"
    n = 0
    with open(tmp, "wb") as g:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            g.write(b)
            n += len(b)
            with lock:
                st["bytes"] += len(b)
    if cl is not None and int(cl) != n:
        os.remove(tmp)
        raise IOError("short %d/%s" % (n, cl))
    os.replace(tmp, dest)
    return 200, n


def worker(idx):
    global active_limit, pause_until
    conn = None
    while not stop:
        if qi >= len(tasks):
            return
        if idx >= active_limit or time.time() < pause_until:
            time.sleep(0.5)
            continue
        t = next_task()
        if t is None:
            return
        hk, path, dest = t
        if os.path.exists(dest):
            with lock:
                st["skip"] += 1
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        for att in range(6):
            try:
                if conn is None:
                    conn = http.client.HTTPSConnection(HOST, timeout=60, context=ctx)
                code, n = fetch(conn, path, dest)
            except Exception as e:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None
                with lock:
                    st["err"] += 1
                failf.write("%s\texc\t%s\n" % (hk, type(e).__name__))
                time.sleep(min(2 ** att, 30))
                continue
            if code == 200:
                with lock:
                    st["ok"] += 1
                break
            if code == 404:
                with lock:
                    st["miss"] += 1
                missf.write("%s\n" % hk)
                break
            # 429/403/5xx
            with lock:
                st["err"] += 1
                if code in (429, 403):
                    st["hard"] += 1
            failf.write("%s\t%d\t\n" % (hk, code))
            try:
                conn.close()
            except Exception:
                pass
            conn = None
            if code in (429, 403):
                with lock:
                    if time.time() >= pause_until:
                        old = active_limit
                        active_limit = max(1, active_limit // 2)
                        pause_until = time.time() + 60
                        log("%d на %s: потоки %d→%d, пауза 60 с" % (code, hk, old, active_limit))
            time.sleep(min(2 ** att, 30))
        else:
            failf.write("%s\tGAVE_UP\t\n" % hk)


maxw = max(steps)
ths = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(maxw)]
for th in ths:
    th.start()

# --- калибровка ---
calib = open(os.path.join(outd, "_calib.tsv"), "a", buffering=1)
res = []
for n in steps:
    with lock:
        active_limit = n
        for k in st:
            st[k] = 0
    t0 = time.time()
    time.sleep(step_s)
    dt = time.time() - t0
    with lock:
        s = dict(st)
    mbs = s["bytes"] / dt / 1e6
    rps = (s["ok"] + s["miss"]) / dt
    row = (n, mbs, rps, s["ok"], s["miss"], s["err"], s["hard"])
    res.append(row)
    calib.write("%s\t%d\t%.1f МБ/с\t%.1f зап/с\tok=%d\t404=%d\tош=%d\t429/403=%d\n" % ((src,) + row))
    log("калибровка %d потоков: %.1f МБ/с, %.1f зап/с, ok=%d 404=%d ош=%d 429/403=%d" % row)
    if s["hard"] > 0 or s["err"] > max(3, 0.02 * (s["ok"] + s["miss"] + s["err"])):
        log("ошибки на ступени %d — подъём остановлен" % n)
        break
clean = [r for r in res if r[5] <= max(3, 0.02 * (r[3] + r[4] + r[5])) and r[6] == 0] or res[:1]
best = max(clean, key=lambda r: r[1] if r[1] > 0 else r[2] * 1e-6)
knee = min((r for r in clean if r[1] >= 0.9 * best[1]), key=lambda r: r[0])
prod = knee[0]
if knee is best and len(clean) > 1 and clean.index(best) > 0:
    prod = clean[clean.index(best) - 1][0]
log("колено %d потоков (лучшая %d, %.1f МБ/с); бой: %d потоков" % (knee[0], best[0], best[1], prod))
calib.write("%s\tбой\t%d\n" % (src, prod))
with lock:
    if time.time() >= pause_until:
        active_limit = min(prod, active_limit) if active_limit < prod else prod
    for k in st:
        st[k] = 0
t0 = time.time()
tot = 0
while any(th.is_alive() for th in ths):
    time.sleep(30)
    with lock:
        s = dict(st)
        tot_b = s["bytes"]
    log("бой: потоков %d, %.1f ГБ, ok=%d 404=%d ош=%d, очередь %d/%d, %.1f МБ/с" % (
        active_limit, tot_b / 1e9, s["ok"], s["miss"], s["err"], qi, len(tasks), tot_b / (time.time() - t0) / 1e6))
log("ГОТОВО: %.1f ГБ, ok=%d 404=%d ош=%d" % (st["bytes"] / 1e9, st["ok"], st["miss"], st["err"]))
