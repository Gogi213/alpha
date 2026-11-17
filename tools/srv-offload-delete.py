#!/usr/bin/env python3
"""TK-586: освободить srv от суток коллекторов, которые уже лежат на Storage Box (sha256-сверено day_upload.py).
Без --go ничего не удаляет (план + проверки). Удаляет только каталог суток <root>/<день>/ при ВСЕХ условиях:
  1. день < --keep-from (UTC); 2. есть <state>/<биржа>/<день>.ok, и размер+mtime каждого локального файла = записи .ok
  (файл не менялся с момента sha256-сверки); 3. на боксе `ls -l` суток: имена и размеры = .ok; 4. в каталоге нет файла
  моложе 15 мин. Файлы pool-<день>*.json в корне и все прочее не трогаются."""
import argparse, json, os, re, shutil, subprocess, sys, time
SRC = {"binance": "/data/collect/binance/data", "okx": "/data/collect/okx/data", "bingx": "/data/collect/bingx/data",
       "bybit": "/data/turbo-probe/soak-bybit/data", "binance-soak": "/data/turbo-probe/soak-binance/data",
       "gate": "/data/collect/gate/data"}
ST = "/data/upload-state"
ap = argparse.ArgumentParser(); ap.add_argument("--keep-from", required=True, help="оставить сутки >= этого дня, напр. 2026-11-15")
ap.add_argument("--go", action="store_true"); a = ap.parse_args()
def box_ls(ex, d):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "box", f"ls -l alpha/collect/{ex}/{d}"], capture_output=True, text=True, timeout=600)
    out = {}
    for line in r.stdout.splitlines():
        m = re.match(r"^-\S+\s+\d+\s+\S+\s+\S+\s+(\d+)\s+\S+\s+\S+\s+\S+\s+(.+)$", line)
        if m: out[m.group(2)] = int(m.group(1))
    out.pop("SHA256SUMS", None); out.pop("UPLOADED.json", None)
    return out
tot = 0
for ex, root in SRC.items():
    for f in sorted(os.listdir(f"{ST}/{ex}")):
        if not f.endswith(".ok"): continue
        d = f[:-3]
        dd = os.path.join(root, d)
        if d >= a.keep_from or not os.path.isdir(dd): continue
        ok = json.load(open(f"{ST}/{ex}/{f}"))["files"]
        loc = {n: os.stat(os.path.join(dd, n)) for n in os.listdir(dd) if os.path.isfile(os.path.join(dd, n))}
        okd = {n: v for n, v in ok.items() if not n.startswith("pool-")}   # pool-<день>.json лежит в корне, а не в каталоге суток
        why = None
        if {n: [s.st_size, s.st_mtime_ns] for n, s in loc.items()} != okd: why = "локально не равно .ok (файлы менялись или появились новые)"
        elif max((s.st_mtime for s in loc.values()), default=0) > time.time() - 900: why = "есть файл моложе 15 мин"
        else:
            rem = box_ls(ex, d)
            if rem.keys() < okd.keys() or any(rem[n] != okd[n][0] for n in okd): why = "бокс: нет файла или размер не равен .ok"
        size = sum(s.st_size for s in loc.values())
        if why: print(f"SKIP {ex} {d} {size/1e9:.2f} ГБ — {why}"); continue
        print(f"{'DEL ' if a.go else 'plan'} {ex} {d} {len(loc)} файлов {size/1e9:.2f} ГБ"); tot += size
        if a.go: shutil.rmtree(dd)
print(f"ИТОГО {'удалено' if a.go else 'будет удалено'} {tot/1e9:.2f} ГБ; свободно сейчас {shutil.disk_usage('/data').free/1e9:.1f} ГБ")
