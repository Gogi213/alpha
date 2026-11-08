"""TK-500: эталон-Python для `pq2bin bybit`: сутки Bybit ob200 (zip jsonl) -> 24 книжных .bin (33 Б, как to_bin.py). Запуск: to_bin_bybit.py <zip> <outdir>"""
import sys, os, zipfile, json, struct
z = zipfile.ZipFile(sys.argv[1]); f = z.open(z.namelist()[0])
hours = [bytearray() for _ in range(24)]; d0 = None; dropped = 0
for line in f:
    if not line.strip(): continue
    m = json.loads(line); ts = m["ts"]
    if d0 is None: d0 = ts - ts % 86400000
    h = (ts - d0) // 3600000
    if not 0 <= h < 24: dropped += 1; continue
    snap = m["type"] == "snapshot"; d = m["data"]; fu = -1 if snap else d["u"]
    for side, lv in ((2, d["b"]), (0, d["a"])):
        for p, q in lv: hours[h] += struct.pack("<qqddB", ts, fu, float(p), float(q), int(snap) | side)
os.makedirs(sys.argv[2], exist_ok=True)
for h, b in enumerate(hours):
    if b: open(f"{sys.argv[2]}/{h:02d}.bin", "wb").write(b)
print("часов", sum(1 for b in hours if b), "вне суток", dropped)
