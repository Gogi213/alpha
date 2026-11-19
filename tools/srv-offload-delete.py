#!/usr/bin/env python3
"""TK-586/TK-608: освободить srv от суток коллекторов, которые уже лежат на Storage Box (sha256-сверено day_upload.py).
Без --go ничего не удаляет (план + проверки). Удаляет только каталог суток <root>/<день>/ при ВСЕХ условиях:
  1. день < границы хранения (--keep-from ДЕНЬ или --keep-hours Ч: день момента «сейчас − Ч часов», UTC);
  2. есть <state>/<биржа>/<день>.ok, и размер+mtime каждого локального файла = записи .ok (файл не менялся с sha256-сверки);
  3. на боксе `ls -l` суток: имена и размеры = .ok; число файлов и сумма байт локально = .ok = бокс;
  4. выборка --sample файлов (случайных): sha256 локально заново = `sha256sum` на боксе;
  5. в каталоге нет файла моложе 15 мин. Файлы pool-<день>*.json в корне и все прочее не трогаются.
Повторный запуск безопасен (flock; удалённые сутки .ok уже не находят каталога)."""
import argparse, datetime, fcntl, hashlib, json, os, random, re, shutil, subprocess, sys, time
SRC = {"binance": "/data/collect/binance/data", "okx": "/data/collect/okx/data", "bingx": "/data/collect/bingx/data",
       "bybit": "/data/turbo-probe/soak-bybit/data", "binance-soak": "/data/turbo-probe/soak-binance/data",
       "gate": "/data/collect/gate/data"}
ST = "/data/upload-state"
ap = argparse.ArgumentParser()
ap.add_argument("--keep-from", help="оставить сутки >= этого дня, напр. 2026-11-15")
ap.add_argument("--keep-hours", type=float, help="оставить сутки, в которых есть данные моложе Ч часов (UTC)")
ap.add_argument("--sample", type=int, default=20, help="файлов суток для повторной sha256-сверки с боксом")
ap.add_argument("--go", action="store_true"); a = ap.parse_args()
if (a.keep_from is None) == (a.keep_hours is None): sys.exit("нужен ровно один из --keep-from / --keep-hours")
keep_from = a.keep_from or datetime.datetime.fromtimestamp(time.time() - a.keep_hours * 3600, datetime.timezone.utc).strftime("%Y-%m-%d")
lock = open(f"{ST}/offload-delete.lock", "w")
try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError: sys.exit("уже идёт другой запуск")
def ssh(cmd):
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "box", cmd], capture_output=True, text=True, timeout=1800)
def box_ls(ex, d):
    r = ssh(f"ls -l alpha/collect/{ex}/{d}")
    out = {}
    for line in r.stdout.splitlines():
        m = re.match(r"^-\S+\s+\d+\s+\S+\s+\S+\s+(\d+)\s+\S+\s+\S+\s+\S+\s+(.+)$", line)
        if m: out[m.group(2)] = int(m.group(1))
    out.pop("SHA256SUMS", None); out.pop("UPLOADED.json", None)
    return out
def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()
def sample_check(ex, d, dd, names):
    """sha256 выборки: локально заново против `sha256sum` на боксе. Возвращает (проверено, строка-причина или None)."""
    pick = random.sample(sorted(names), min(a.sample, len(names)))
    if not pick: return 0, None
    r = ssh("sha256sum " + " ".join(f"alpha/collect/{ex}/{d}/{n}" for n in pick))
    rem = {os.path.basename(p.strip()): h for h, _, p in (l.partition("  ") for l in r.stdout.splitlines())}
    bad = [n for n in pick if rem.get(n) != sha256(os.path.join(dd, n))]
    return len(pick), (f"sha256 выборки не сошёлся: {bad[:3]}" if bad else None)
tot = 0
print(f"граница хранения: сутки < {keep_from} (UTC), выборка sha256 {a.sample}", flush=True)
for ex, root in SRC.items():
    if not os.path.isdir(f"{ST}/{ex}"): continue
    for f in sorted(os.listdir(f"{ST}/{ex}")):
        if not f.endswith(".ok"): continue
        d = f[:-3]
        dd = os.path.join(root, d)
        if d >= keep_from or not os.path.isdir(dd): continue
        ok = json.load(open(f"{ST}/{ex}/{f}"))["files"]
        loc = {n: os.stat(os.path.join(dd, n)) for n in os.listdir(dd) if os.path.isfile(os.path.join(dd, n))}
        okd = {n: v for n, v in ok.items() if not n.startswith("pool-")}   # pool-<день>.json лежит в корне, а не в каталоге суток
        why, checked = None, 0
        if {n: [s.st_size, s.st_mtime_ns] for n, s in loc.items()} != okd: why = "локально не равно .ok (файлы менялись или появились новые)"
        elif max((s.st_mtime for s in loc.values()), default=0) > time.time() - 900: why = "есть файл моложе 15 мин"
        else:
            rem = box_ls(ex, d)
            if rem.keys() < okd.keys() or any(rem[n] != okd[n][0] for n in okd): why = "бокс: нет файла или размер не равен .ok"
            elif sum(rem[n] for n in okd) != sum(s.st_size for s in loc.values()): why = "бокс: сумма байт не равна локальной"
            else: checked, why = sample_check(ex, d, dd, okd)
        size = sum(s.st_size for s in loc.values())
        if why: print(f"SKIP {ex} {d} {size/1e9:.2f} ГБ — {why}", flush=True); continue
        print(f"{'DEL ' if a.go else 'plan'} {ex} {d} {len(loc)} файлов {size} байт ({size/1e9:.2f} ГБ), "
              f"бокс: те же имена и размеры, sha256 выборки {checked}/{checked}", flush=True); tot += size
        if a.go: shutil.rmtree(dd)
print(f"ИТОГО {'удалено' if a.go else 'будет удалено'} {tot/1e9:.2f} ГБ; свободно сейчас {shutil.disk_usage('/data').free/1e9:.1f} ГБ", flush=True)
