#!/usr/bin/env python3
# TK-071: время одного случайного чтения 4 КиБ с HDD (O_DIRECT) — SEEK_S для judge_window; запуск как замер:
# systemd-run --unit tk071-seek --collect /data/benchrun.sh wave python3 /data/sched/sched-seek.py
import os, random, time, mmap, statistics
for dev in ("/dev/sdb", "/dev/sda"):
    fd = os.open(dev, os.O_RDONLY | os.O_DIRECT)
    size = os.lseek(fd, 0, os.SEEK_END)
    buf = mmap.mmap(-1, 4096)
    random.seed(7)
    lat = []
    for _ in range(1500):
        off = random.randrange(0, size // 4096) * 4096
        t = time.perf_counter()
        os.preadv(fd, [buf], off)
        lat.append((time.perf_counter() - t) * 1000)
    lat.sort()
    print(f"{dev} n={len(lat)} mean_ms={statistics.mean(lat):.2f} p50={lat[len(lat)//2]:.2f} p95={lat[int(len(lat)*.95)]:.2f} max={lat[-1]:.2f}", flush=True)
    os.close(fd)
