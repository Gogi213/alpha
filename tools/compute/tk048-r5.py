#!/usr/bin/env python3
# tk048-r5.py <S> <queue.txt> <G> [читателей на диск=1] [лимит_ГБ=22]: К-5 (TK-048, Судья 09.10 08:05). Из tk048-r1.py (READY_D=1 всегда),
# но порядок единиц — строки готовой очереди "сутки k" (HOIST/QTOP сохраняются), а не "группа -> сутки". Единица уходит в stdout,
# когда в кэше её сайдкары .events и бинлоги суток D; файлы D+1 встают в очередь чтения на AHEAD единиц позже и дочитываются,
# пока единица считает. Один читатель на диск (по st_dev) в порядке очереди единиц, внутри единицы — по физическому адресу (filefrag).
# Окно по байтам (лимит_ГБ), DONTNEED файла, когда готовы все единицы, которым он нужен. Печать: стат читателя раз в 5 с в stderr.
import datetime, os, subprocess, sys, threading, time
READY_D = True
AHEAD = int(os.environ.get("AHEAD", "15"))
S, QF, G = sys.argv[1], sys.argv[2], int(sys.argv[3])
NR = int(sys.argv[4]) if len(sys.argv) > 4 else 1
LIM = (float(sys.argv[5]) if len(sys.argv) > 5 else 22.0) * 1e9
CH = 16 << 20

def next_day(d):
    return (datetime.date.fromisoformat(d) + datetime.timedelta(days=1)).isoformat()

def side_of(d, k):
    L = open(os.path.realpath(f"{S}/study/root-{d}/instruments.csv"), newline='').read().split(chr(10))
    rows = [l.split(',')[0] for l in L[1:] if l]
    n = len(rows)
    return [f"{S}/study/root-{dd}/{s}-{dd}.binlog.events" for dd in (d, next_day(d)) for s in rows[k * n // G:(k + 1) * n // G]]

def phys(p):
    try:
        o = subprocess.run(["filefrag", "-v", p], capture_output=True, text=True).stdout
        for l in o.splitlines():
            f = l.split()
            if f and f[0].rstrip(':') == "0":
                return int(f[3].split('..')[0])
    except (OSError, ValueError, IndexError):
        pass
    return 0

def files_of(d, k):
    L = open(os.path.realpath(f"{S}/study/root-{d}/instruments.csv"), newline='').read().split('\n')
    rows = [l.split(',')[0] for l in L[1:] if l]
    n = len(rows)
    out = []
    for dd in ((d,) if READY_D else (d, next_day(d))):
        for s in rows[k * n // G:(k + 1) * n // G]:
            p = f"{S}/root/{s}-{dd}.binlog"
            if os.path.exists(p):
                out.append(os.path.realpath(p))
    return sorted(out, key=phys)

units = [(l.split()[0], int(l.split()[1])) for l in open(QF) if l.strip()]
F = {u: files_of(*u) for u in units}
users = {}
for u, fs in F.items():
    for p in fs:
        users.setdefault(p, set()).add(u)
EXTRA = {}
if READY_D:
    for u in units:
        full = [p for p in dict.fromkeys(os.path.realpath(f"{S}/root/{s}-{next_day(u[0])}.binlog") for s in
                [os.path.basename(x).rsplit('-', 3)[0] for x in F[u]]) if os.path.exists(p)]
        EXTRA[u] = full
        for p in full:
            users.setdefault(p, set()).add(u)
cdone, lock = {}, threading.Lock()
cached = [0]
ready = {u: threading.Event() for u in units}
total = [0]

def read_file(path):
    buf = bytearray(CH)
    fd = os.open(path, os.O_RDONLY)
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_SEQUENTIAL)
        n = 0
        while True:
            m = os.readv(fd, [buf])
            if not m:
                return n
            n += m
    finally:
        os.close(fd)

def reader(q):
    while True:
        with lock:
            if not q:
                return
            p = q.pop(0)
        while True:
            with lock:
                if cached[0] <= LIM:
                    break
            time.sleep(0.2)
        sz = read_file(p)
        with lock:
            cached[0] += sz
            total[0] += sz
        cdone[p].set()

sc_done = {u: threading.Event() for u in units}

def warm_side(q):
    while True:
        with lock:
            if not q:
                return
            u = q.pop(0)
        for p in side_of(*u):
            try:
                with open(p, 'rb') as f:
                    f.read()
            except OSError:
                pass
        sc_done[u].set()

def waiter(u):
    for p in F[u]:
        cdone[p].wait()
    if READY_D:
        sc_done[u].wait()
    ready[u].set()

def dropper():
    done, dropped = set(), set()
    while len(done) < len(units):
        time.sleep(1)
        for fn in os.listdir(S):
            if fn.startswith("done-"):
                q = fn.split("-")
                done.add(("-".join(q[1:4]), int(q[4])))
        for p, us in users.items():
            if p in dropped or not us <= done or p not in cdone or not cdone[p].is_set():
                continue
            dropped.add(p)
            try:
                fd = os.open(p, os.O_RDONLY)
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
                os.close(fd)
                with lock:
                    cached[0] -= os.path.getsize(p)
            except OSError:
                pass

threading.Thread(target=dropper, daemon=True).start()
order, qs = [], {}
def enqueue(p):
    if p not in cdone:
        cdone[p] = threading.Event()
        qs.setdefault(os.stat(p).st_dev, []).append(p)
for i, u in enumerate(units):
    for p in F[u]:
        enqueue(p)
    if READY_D and i >= AHEAD:
        for p in EXTRA[units[i - AHEAD]]:
            enqueue(p)
if READY_D:
    for u in units[-AHEAD:]:
        for p in EXTRA[u]:
            enqueue(p)
    sq = list(units)
    for _ in range(4):
        threading.Thread(target=warm_side, args=(sq,), daemon=True).start()
for q in qs.values():
    for _ in range(NR):
        threading.Thread(target=reader, args=(q,), daemon=True).start()
for u in units:
    threading.Thread(target=waiter, args=(u,), daemon=True).start()
t0 = time.time()
def stat():
    while True:
        time.sleep(5)
        with lock:
            sys.stderr.write(f"r5 t={time.time()-t0:.0f} read_GB={total[0]/1e9:.2f} cached_GB={cached[0]/1e9:.2f} ready={sum(e.is_set() for e in ready.values())}\n")
threading.Thread(target=stat, daemon=True).start()
for u in units:
    ready[u].wait()
    print(u[0], u[1], flush=True)
sys.stderr.write(f"r5: прочитано {total[0]/1e9:.1f} ГБ, последняя единица выдана на {time.time()-t0:.0f} с\n")
os._exit(0)
