#!/usr/bin/env python3
"""TK-259: разведка I/O на calc — ОЗУ (кэш страниц, tmpfs) против HDD, порядок и повторы чтений. Только чтение входов.

Единица: сутки D, группа k из G (каждый G-й символ по имени): approaches-<SYM>.csv (sdb), <SYM>.csv.abin (sdb),
<SYM>-D.binlog и .binlog.carry-events (sda, как у волны — довесок D+1 не читаем);
csv_ab = те csv, у которых есть abin (сравнение E5 на одних символах). Запуск ТОЛЬКО заявкой alsched (--kind gate --cores 3):
  tk259-io-probe.py plan                      — размеры единицы, без чтений
  tk259-io-probe.py run [--reps 5] [--exp 1,2,3,4,5]
env: D=2026-01-15 K=0 G=8 E=/data/tk046/jan/home/alpha/epochs/e-jan AB=/data/tk048/abin-t46m/study/approaches/D20
     OUT=/data/tk0259/out SHM=/dev/shm/tk259 SHM_CAP_GB=8
Холодный кэш — НЕ drop_caches (глобальный, портит чужой кэш), а posix_fadvise(DONTNEED) по файлам единицы; проверка:
диск_МБ холодного чтения ≥ 0,9 × размер. Диск — /proc/diskstats (iostat на calc нет): МБ, io_ticks/стена = занятость, ЦП = process_time.
readahead — /sys/block/<диск>/queue/read_ahead_kb, возвращается в finally."""
import fcntl
import glob
import json
import os
import random
import shutil
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor

D = os.environ.get("D", "2026-01-15")
K = int(os.environ.get("K", "0"))
G = int(os.environ.get("G", "8"))
E = os.environ.get("E", "/data/tk046/jan/home/alpha/epochs/e-jan")
AB = os.environ.get("AB", "/data/tk048/abin-t46m/study/approaches/D20")
OUT = os.environ.get("OUT", "/data/tk0259/out")
SHM = os.environ.get("SHM", "/dev/shm/tk259")
SHM_CAP = float(os.environ.get("SHM_CAP_GB", "8")) * 2**30
DISKS = ("sda", "sdb")
CHUNK = 1 << 20


def unit():
    """-> {'csv': [...], 'abin': [...], 'ev': [...]} реальные пути (симлинки раскрыты)."""
    syms = sorted(os.path.basename(p)[len("approaches-"):-len(".csv")] for p in glob.glob(f"{E}/study/approaches/D20/{D}/approaches-*.csv"))
    syms = syms[K::G]
    u = {"csv": [], "abin": [], "ev": [], "csv_ab": []}
    for s in syms:
        for key, p in (("csv", f"{E}/study/approaches/D20/{D}/approaches-{s}.csv"), ("abin", f"{AB}/{D}/approaches-{s}.csv.abin"),
                       ("ev", f"{E}/root/{s}-{D}.binlog"), ("ev", f"{E}/root/{s}-{D}.binlog.carry-events")):
            if os.path.exists(p):
                u[key].append(os.path.realpath(p))
        if os.path.exists(f"{AB}/{D}/approaches-{s}.csv.abin") and os.path.exists(f"{E}/study/approaches/D20/{D}/approaches-{s}.csv"):
            u["csv_ab"].append(os.path.realpath(f"{E}/study/approaches/D20/{D}/approaches-{s}.csv"))
    return syms, u


def size(files):
    return sum(os.path.getsize(f) for f in files)


def phys_key(path):
    """(диск-устройство, физическое смещение первого экстента) через FIEMAP; нет — (st_dev, 0)."""
    st = os.stat(path)
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            buf = bytearray(32 + 56)
            struct.pack_into("<QQIIII", buf, 0, 0, 2**64 - 1, 0, 1, 1, 0)
            fcntl.ioctl(fd, 0xC020660B, buf)
            mapped = struct.unpack_from("<I", buf, 20)[0]
            phys = struct.unpack_from("<Q", buf, 32 + 8)[0] if mapped else 0
        finally:
            os.close(fd)
    except OSError:
        phys = 0
    return (st.st_dev, phys)


def evict(files):
    for f in files:
        fd = os.open(f, os.O_RDONLY)
        try:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        finally:
            os.close(fd)


def read_all(path):
    n = 0
    fd = os.open(path, os.O_RDONLY)
    try:
        while True:
            b = os.read(fd, CHUNK)
            if not b:
                return n
            n += len(b)
    finally:
        os.close(fd)


def ds():
    r = {}
    for line in open("/proc/diskstats"):
        p = line.split()
        if p[2] in DISKS:
            r[p[2]] = (int(p[5]) * 512, int(p[12]))  # байт прочитано, io_ticks мс
    return r


def timed(files, P=1):
    """Читает files (порядок списка) P потоками -> метрики."""
    d0, c0, t0 = ds(), time.process_time(), time.perf_counter()
    if P == 1:
        total = sum(read_all(f) for f in files)
    else:
        with ThreadPoolExecutor(P) as ex:
            total = sum(ex.map(read_all, files))
    wall = time.perf_counter() - t0
    cpu = time.process_time() - c0
    d1 = ds()
    m = {"wall_s": round(wall, 3), "MB": round(total / 2**20, 1), "MBps": round(total / 2**20 / wall, 1) if wall else 0,
         "cpu_s": round(cpu, 3)}
    for d in DISKS:
        m[f"{d}_MB"] = round((d1[d][0] - d0[d][0]) / 2**20, 1)
        m[f"{d}_busy_pct"] = round(100 * (d1[d][1] - d0[d][1]) / 1000 / wall, 1) if wall else 0
    return m


def ra_get(d):
    return int(open(f"/sys/block/{d}/queue/read_ahead_kb").read())


def ra_set(d, kb):
    open(f"/sys/block/{d}/queue/read_ahead_kb", "w").write(str(kb))


class Res:
    def __init__(self):
        os.makedirs(OUT, exist_ok=True)
        self.f = open(f"{OUT}/result.jsonl", "a")

    def add(self, exp, cell, rep, m):
        rec = {"exp": exp, "cell": cell, "rep": rep, **m}
        self.f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.f.flush()
        print(json.dumps(rec, ensure_ascii=False), flush=True)


def to_shm(files, sub):
    dst = f"{SHM}/{sub}"
    os.makedirs(dst, exist_ok=True)
    out = []
    for f in files:
        p = f"{dst}/{os.path.basename(f)}"
        shutil.copyfile(f, p)
        out.append(p)
    return out


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "plan"
    syms, u = unit()
    allf = u["csv"] + u["ev"]
    sz = {k: round(size(v) / 2**20, 1) for k, v in u.items()}
    allf = u["csv"] + u["ev"]
    print(f"единица D={D} k={K}/G={G}: символов {len(syms)}; файлы csv {len(u['csv'])} ev {len(u['ev'])} abin {len(u['abin'])} csv_ab {len(u['csv_ab'])}; МБ {sz}")
    if mode == "plan":
        return
    reps = int(sys.argv[sys.argv.index("--reps") + 1]) if "--reps" in sys.argv else 5
    exps = (sys.argv[sys.argv.index("--exp") + 1] if "--exp" in sys.argv else "1,2,3,4,5").split(",")
    need = size(allf) + size(u["abin"])
    if need > SHM_CAP:
        sys.exit(f"единица {need / 2**30:.1f} ГБ > лимита tmpfs {SHM_CAP / 2**30:.0f} ГБ — уменьшить (G больше)")
    R = Res()
    R.add("meta", "unit", 0, {"D": D, "k": K, "G": G, "syms": len(syms), "MB": sz, "reps": reps,
                              "ra_kb": {d: ra_get(d) for d in DISKS}, "mem_avail_MB": int(open("/proc/meminfo").read().split("MemAvailable:")[1].split()[0]) // 1024})
    ra0 = {d: ra_get(d) for d in DISKS}
    srt = sorted(allf)
    keys = {f: phys_key(f) for f in allf}
    phys = sorted(allf, key=lambda f: keys[f])
    try:
        if "1" in exps:  # холодное с HDD vs повтор из кэша страниц
            for r in range(reps):
                evict(allf)
                R.add(1, "cold_hdd", r, timed(srt))
                R.add(1, "warm_cache", r, timed(srt))
        if "2" in exps:  # tmpfs: те же входы
            t0 = time.perf_counter()
            evict(allf)
            sh = to_shm(allf, "u")
            R.add(2, "copy_to_shm_cold", 0, {"wall_s": round(time.perf_counter() - t0, 3), "MB": round(size(sh) / 2**20, 1)})
            ssh = sorted(sh)
            for r in range(reps):
                R.add(2, "tmpfs", r, timed(ssh))
            shutil.rmtree(f"{SHM}/u", ignore_errors=True)
        if "3" in exps:  # порядок × параллельность × readahead (холодный кэш)
            rnd = list(allf)
            random.Random(1).shuffle(rnd)
            for ra in ("cur", 128):
                for d in DISKS:
                    ra_set(d, ra0[d] if ra == "cur" else ra)
                for order, files in (("random", rnd), ("physical", phys)):
                    for P in (1, 8):
                        for r in range(reps):
                            evict(allf)
                            R.add(3, f"{order}_P{P}_ra{ra if ra != 'cur' else ra0['sdb']}", r, timed(files, P))
            for d in DISKS:
                ra_set(d, ra0[d])
        if "4" in exps:  # одни сутки N=8 раз (группы G=8): общий кэш / без кэша / tmpfs
            N = 8
            for r in range(reps):
                evict(allf)
                ms = [timed(phys) for _ in range(N)]
                R.add(4, "shared_cache_x8", r, {"wall_s": round(sum(m["wall_s"] for m in ms), 3), "cpu_s": round(sum(m["cpu_s"] for m in ms), 3),
                                               "sda_MB": round(sum(m["sda_MB"] for m in ms), 1), "sdb_MB": round(sum(m["sdb_MB"] for m in ms), 1),
                                               "first_s": ms[0]["wall_s"], "next_avg_s": round(sum(m["wall_s"] for m in ms[1:]) / (N - 1), 3)})
            for r in range(reps):
                ms = []
                for _ in range(N):
                    evict(allf)
                    ms.append(timed(phys))
                R.add(4, "no_cache_x8", r, {"wall_s": round(sum(m["wall_s"] for m in ms), 3), "cpu_s": round(sum(m["cpu_s"] for m in ms), 3),
                                           "sda_MB": round(sum(m["sda_MB"] for m in ms), 1), "sdb_MB": round(sum(m["sdb_MB"] for m in ms), 1)})
            sh = to_shm(allf, "u4")
            for r in range(reps):
                ms = [timed(sorted(sh)) for _ in range(N)]
                R.add(4, "tmpfs_x8", r, {"wall_s": round(sum(m["wall_s"] for m in ms), 3), "cpu_s": round(sum(m["cpu_s"] for m in ms), 3)})
            shutil.rmtree(f"{SHM}/u4", ignore_errors=True)
        if "5" in exps:  # approaches: csv vs abin, HDD холодный vs tmpfs
            for nm, key in (("csv", "csv_ab"), ("abin", "abin")):
                fs = sorted(u[key])
                for r in range(reps):
                    evict(fs)
                    R.add(5, f"{nm}_cold_hdd", r, timed(fs))
                sh = to_shm(fs, f"a-{nm}")
                for r in range(reps):
                    R.add(5, f"{nm}_tmpfs", r, timed(sorted(sh)))
                shutil.rmtree(f"{SHM}/a-{nm}", ignore_errors=True)
    finally:
        for d in DISKS:
            try:
                ra_set(d, ra0[d])
            except OSError:
                pass
        shutil.rmtree(SHM, ignore_errors=True)
        R.add("meta", "cleanup", 0, {"shm_left": os.path.exists(SHM), "ra_kb": {d: ra_get(d) for d in DISKS}})
    open(f"{OUT}/done", "w").write(time.strftime("%F %T"))


if __name__ == "__main__":
    main()
