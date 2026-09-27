#!/usr/bin/env python3
"""П-07: очередь «клетка x сутки» для Г-85а/Г-85б (база, H6, H7, H2), память-ограда (CEO 27.09).

Порядок (CEO): базы (оба варианта, все 54 суток) -> H6+H7 (оба варианта) -> H2. Как только обе
базы полностью посчитаны на всех 54 сутках - один раз запускается H9/H10 (busy-replay поверх
p07a-base/p07b-base) в этом же процессе, не блокируя дальнейшую очередь (отдельный поток).

Ограда памяти (Инженер 27.09): пик bounce-grid ~2.5 ГБ на обычных сутках, до ~5.7 ГБ на
крупнейших символ-сутках (запись 16-23.09, LSK 14-15.09) - список BIG_DAYS ниже по замеру байт
binlog. Новый процесс стартует только если MemAvailable >= MEM_BIG_GB (крупные сутки) или
MEM_NORMAL_GB (прочие), и всегда под systemd-run --user --scope -p MemoryMax=.. -p MemorySwapMax=0
(общий OOM не даст один процесс уронить все воркеры).

    python3 p07-sched.py            # старт всей очереди
    python3 p07-sched.py --status   # что готово / что в очереди
"""
import fcntl
import os
import subprocess
import sys
import time

A = os.path.expanduser("~/alpha")
BIN = "bin/alpha-9ffd80c"
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "7"))
MEM_NORMAL_GB = float(os.environ.get("MEM_NORMAL_GB", "3.0"))
MEM_BIG_GB = float(os.environ.get("MEM_BIG_GB", "6.0"))
GAP_S = int(os.environ.get("GAP_S", "20"))
SCOPE_MEM_MAX = os.environ.get("SCOPE_MEM_MAX", "7G")
THREADS = os.environ.get("THREADS", "1")
OUT_ROOT = f"{A}/tmp-p07"
LOG = open(f"{OUT_ROOT}/sched.log", "a", buffering=1)
H9H10_FLAG = f"{OUT_ROOT}/.h9h10-triggered"

HOMES = [
    ("aug", f"{A}/epochs/e-aug", [f"2026-08-{d:02d}" for d in range(1, 32)]),
    ("sep1", f"{A}/tmp-lsk0914.used-20260926/home", [f"2026-09-{d:02d}" for d in range(1, 16)]),
    ("sep2", f"{A}/tmp-t29/rec", [f"2026-09-{d:02d}" for d in range(16, 24)]),
]
BIG_DAYS = {  # binlog bytes > 3.5e9 (замер 27.09, /tmp/daysizes.txt) - нужна ограда 6 ГБ, не 3
    ("sep1", "2026-09-14"), ("sep1", "2026-09-15"),
    ("sep2", "2026-09-16"), ("sep2", "2026-09-17"), ("sep2", "2026-09-18"), ("sep2", "2026-09-19"),
    ("sep2", "2026-09-20"), ("sep2", "2026-09-21"), ("sep2", "2026-09-22"), ("sep2", "2026-09-23"),
}

RTT = ("--median-rtt-ns place=4200000,cancel=3980000,taker=5650000 "
       "--p95-rtt-ns place=4790000,cancel=4550000,taker=6420000")
COMMON = (f"--signal approach --queue-model prob:3 {RTT} --regime-from study/regime "
          "--order-usd 500 --carry-root root --h3-mode notional --h3-usd 10000 "
          "--entry-ttl-secs 1800 --band-exit-bps 20 --exit-form none --busy-skip off")

BASE_SET_NAME = "t-bid-btc4h-q1"
BASE_SET = "age=2700,side=bid,btc4h_max=-44.55"

# (variant, axis, out_suffix, entry_form, stop_form, take_form, deadline_secs, set_name, setspec)
CELLS = []


def add(variant, axis, suffix, entry, stop, take, dl=14400, set_name=BASE_SET_NAME, setspec=BASE_SET):
    CELLS.append((variant, axis, suffix, entry, stop, take, dl, set_name, setspec))


# В-131 (владелец, 27.09): Г-85б (ladder в фиксированных bps) снята из очереди - форма неверна,
# нужна новая (шаг цены + волатильность), не в этом протоколе. Освободившиеся слоты - Г-85а
# ступень 3 (H3/H4/H5/H8) ниже. H1 (frontrun_min) не ставится - пороги p25/p50/p75 сначала на базе.
add("a", "base", "base", "single@fr", "pct2", "tr1x1")

for stop in ("pct1.5", "pct3", "before"):
    add("a", "H6", f"h6-{stop}", "single@fr", stop, "tr1x1")
for take in ("tr1.5x1", "tr1x1.5", "tr2x1", "1to1"):
    add("a", "H7", f"h7-{take}", "single@fr", "pct2", take)
for off in (1, 2, 3):
    add("a", "H2", f"h2-fr{off}", f"single@fr+{off}", "pct2", "tr1x1")

for v in (25000, 50000, 100000):
    add("a", "H3", f"h3-{v}", "single@fr", "pct2", "tr1x1",
        setspec=f"age=2700,side=bid,btc4h_max=-44.55,usd_min={v}")
for v in (900, 1800, 3600, 5400):
    add("a", "H4", f"h4-{v}", "single@fr", "pct2", "tr1x1",
        setspec=f"age={v},side=bid,btc4h_max=-44.55")
add("a", "H5", "h5-btc1h", "single@fr", "pct2", "tr1x1",
    set_name="t-bid-btc1h-q1", setspec="age=2700,side=bid,btc1h_max=-21.17")
add("a", "H5", "h5-btc2h", "single@fr", "pct2", "tr1x1",
    set_name="t-bid-btc2h-q1", setspec="age=2700,side=bid,btc2h_max=-30.56")
add("a", "H5", "h5-btc3h", "single@fr", "pct2", "tr1x1",
    set_name="t-bid-btc3h-q1", setspec="age=2700,side=bid,btc3h_max=-38.75")
for v in (3600, 7200):
    add("a", "H8", f"h8-{v}", "single@fr", "pct2", "tr1x1", dl=v)

PRIORITY = {"base": 0, "H6": 1, "H7": 1, "H2": 2, "H3": 3, "H4": 3, "H5": 3, "H8": 3}
CELLS.sort(key=lambda c: PRIORITY[c[1]])


def say(msg):
    LOG.write(f"== {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}\n")


def max_workers():
    """Число воркеров: файл tmp-p07/max_workers (правится на ходу, без перезапуска), иначе MAX_WORKERS."""
    try:
        return int(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "max_workers")).read().strip())
    except (OSError, ValueError):
        return MAX_WORKERS


def lock_held(home, day, out_name):
    """Сутки уже считает процесс, переживший перезапуск планировщика (держит flock своего дня)."""
    path = os.path.join(home, f"study/.day-lock-{day}-{out_name}")
    if not os.path.exists(path):
        return False
    with open(path, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(f, fcntl.LOCK_UN)
    return False


def mem_avail_gb():
    for line in open("/proc/meminfo"):
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1024 / 1024
    return 0.0


def out_dir_name(variant, suffix):
    return f"p07{variant}-{suffix}"


def day_done(home, out_name, day, first_set="t-bid-btc4h-q1"):
    d = f"{home}/b5/{out_name}/{day}"
    if os.path.exists(f"{d}/.done"):
        return True
    log = f"{d}.log"
    if os.path.exists(f"{d}/{first_set}/forms.csv") and os.path.exists(log):
        with open(log, "rb") as f:
            if any(l.startswith(b"bounce-grid: \xd1\x84\xd0\xbe\xd1\x80\xd0\xbc ") for l in f):
                open(f"{d}/.done", "w").close()
                return True
    return False


def build_tasks():
    tasks = []
    for cell in CELLS:
        variant, axis, suffix, entry, stop, take, dl, set_name, setspec = cell
        out_name = out_dir_name(variant, suffix)
        per_home = []
        for bname, home, days in HOMES:
            per_home.append([(cell, out_name, bname, home, day) for day in days])
        mixed = []
        while any(per_home):
            for lst in per_home:
                if lst:
                    mixed.append(lst.pop(0))
        tasks += mixed
    return tasks


def base_all_done():
    out_name = out_dir_name("a", "base")
    for bname, home, days in HOMES:
        for day in days:
            if not day_done(home, out_name, day):
                return False
    return True


def trigger_h9h10():
    if os.path.exists(H9H10_FLAG):
        return
    open(H9H10_FLAG, "w").close()
    say("база Г-85а готова (все 54 суток) - запуск H9/H10 (busy-replay) фоном")
    script = f"{OUT_ROOT}/p07-h9h10-run.sh"
    subprocess.Popen(["bash", script], cwd=A,
                      stdout=open(f"{OUT_ROOT}/h9h10.log", "a"),
                      stderr=subprocess.STDOUT)


def main():
    if "--status" in sys.argv:
        tasks = build_tasks()
        done = sum(1 for t in tasks if day_done(t[3], t[1], t[4], t[0][7]))
        print(f"{done}/{len(tasks)} задач готово; база Г-85а готова: {base_all_done()}")
        return
    tasks = build_tasks()
    running = {}
    last_start = 0.0
    say(f"очередь: {len(tasks)} клетка-суток, MAX_WORKERS={MAX_WORKERS}, "
        f"MEM_NORMAL={MEM_NORMAL_GB}, MEM_BIG={MEM_BIG_GB}, THREADS={THREADS}")
    h9h10_done = os.path.exists(H9H10_FLAG)
    while tasks or running:
        for pid, (t, p) in list(running.items()):
            rc = p.poll()
            if rc is None:
                continue
            cell, out_name, bname, home, day = t
            out = f"{home}/b5/{out_name}/{day}"
            if rc == 0:
                open(f"{out}/.done", "w").close()
                say(f"{cell[1]} {out_name} {bname} {day}: готово")
            else:
                say(f"{cell[1]} {out_name} {bname} {day}: ОШИБКА код {rc} - сутки в конец очереди")
                subprocess.run(["rm", "-rf", out])
                tasks.append(t)
            del running[pid]
        while tasks and day_done(tasks[0][3], tasks[0][1], tasks[0][4], tasks[0][0][7]):
            tasks.pop(0)
        if not h9h10_done and base_all_done():
            trigger_h9h10()
            h9h10_done = True
        if tasks:
            t = tasks[0]
            cell, out_name, bname, home, day = t
            big = (bname, day) in BIG_DAYS
            need = MEM_BIG_GB if big else MEM_NORMAL_GB
            ok = (time.time() - last_start >= GAP_S and len(running) < max_workers()
                  and mem_avail_gb() >= need)
            if (ok or not running) and lock_held(home, day, out_name):
                tasks.append(tasks.pop(0))  # считается осиротевшим процессом после перезапуска — в конец, не трогать
                time.sleep(1)
                continue
            if ok or not running:
                tasks.pop(0)
                variant, axis, suffix, entry, stop, take, dl, set_name, setspec = cell
                os.makedirs(f"{home}/b5/{out_name}", exist_ok=True)
                if os.path.isdir(f"{home}/b5/{out_name}/{day}"):
                    subprocess.run(["rm", "-rf", f"{home}/b5/{out_name}/{day}"])
                scope = (f"systemd-run --user --scope --quiet -p MemoryMax={SCOPE_MEM_MAX} "
                         f"-p MemorySwapMax=0")
                out_rel = f"b5/{out_name}/{day}"
                cmd = (f"exec 9>study/.day-lock-{day}-{out_name}; flock 9; "
                       f"{scope} nice -n 15 {BIN} lob bounce-grid --root study/root-{day} "
                       f"--touches-from study/approaches/D20 {COMMON} "
                       f"--entry-form '{entry}' --stop-form '{stop}' --take-form '{take}' "
                       f"--deadline-secs {dl} --set {set_name}:{setspec} --threads {THREADS} "
                       f"--out-dir {out_rel} > {out_rel}.log 2>&1")
                p = subprocess.Popen(["bash", "-c", cmd], cwd=home)
                running[p.pid] = (t, p)
                last_start = time.time()
                say(f"{cell[1]} {out_name} {bname} {day}: старт (идёт {len(running)}, "
                    f"MemAvail {mem_avail_gb():.1f} ГБ, big={big})")
                continue
        time.sleep(8)
    say("очередь пуста")


if __name__ == "__main__":
    main()
