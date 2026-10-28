#!/usr/bin/env python3
"""TK-445 (копия TK-420, список задач из файла tk445-tasks.py): public.bybit.com linear — дневные, spot — месячные файлы сделок.
Потолок 4 потока (бан 23.10 при 32, Судья 26.10: ≤4); калибровка потоков (В-178): колено = наименьшая ступень с ≥90 % лучшей скорости при нуле ошибок.
Первый 429/403 — потоки вдвое и пауза 60 с. 404 — не ошибка (_missing.tsv). Перезапуск пропускает готовое; готовое в ALT-корнях (через :) засчитывается без закачки.
Использование: tk445-dl.py <задачи.tsv> <каталог выхода> [--steps 1,2,4] [--step-s 30] [--alt корень:корень] [--head]
--head — только HEAD по всем задачам (4 потока): _sizes.tsv (путь<TAB>байт|404), ничего не качает.
Выход: <каталог>/bybit-linear|bybit-spot/<СИМ>/<файл>; ход — <каталог>/_calib.tsv, _progress.log, _missing.tsv, _fails.tsv."""
import http.client
import os
import ssl
import sys
import threading
import time

src = "bybit"
listf, outd = sys.argv[1:3]
steps = [1, 2, 4]
step_s = 30
if "--steps" in sys.argv:
    steps = [int(x) for x in sys.argv[sys.argv.index("--steps") + 1].split(",")]
if "--step-s" in sys.argv:
    step_s = int(sys.argv[sys.argv.index("--step-s") + 1])
ALT = sys.argv[sys.argv.index("--alt") + 1].split(":") if "--alt" in sys.argv else []
HEADONLY = "--head" in sys.argv
os.makedirs(outd, exist_ok=True)
HOST = "public.bybit.com"

tasks = []
for ln in open(listf, encoding="utf-8"):
    f = ln.rstrip(chr(10)).split(chr(9))
    if len(f) == 4:
        tasks.append(("bybit %s %s %s" % (f[0], f[1], os.path.basename(f[3])), f[2], os.path.join(outd, f[3]), f[3]))
print("задач: %d" % len(tasks), flush=True)

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
sizef = open(os.path.join(outd, "_sizes.tsv"), "a", buffering=1)


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
        hk, path, dest, rel = t
        if HEADONLY:
            try:
                if conn is None:
                    conn = http.client.HTTPSConnection(HOST, timeout=60, context=ctx)
                conn.request("HEAD", path, headers={"User-Agent": "Mozilla/5.0"})
                r = conn.getresponse()
                r.read()
                sizef.write("%s\t%s\n" % (rel, r.getheader("Content-Length") if r.status == 200 else r.status))
                with lock:
                    st["ok"] += 1
            except Exception as e:
                conn = None
                failf.write("%s\thead-exc\t%s\n" % (hk, type(e).__name__))
            continue
        if os.path.exists(dest) or any(os.path.exists(os.path.join(a, rel)) for a in ALT):
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

if HEADONLY:
    active_limit = 4
    t0 = time.time()
    while any(th.is_alive() for th in ths):
        time.sleep(10)
        log("head: %d/%d, очередь %d" % (st["ok"], len(tasks), qi))
    log("HEAD ГОТОВО за %.0f с" % (time.time() - t0))
    sys.exit(0)

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
