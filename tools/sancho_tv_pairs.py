"""Сверка файлов выгрузки (photos/) со снимками TV (data/sancho-tv/): попиксельная корреляция серых миниатюр.
Для каждого файла — лучший снимок среди ссылок цепочки и корреляция; ниже порога — на ручную проверку глазами."""
import csv, sys, numpy as np
from PIL import Image
D = "C:/Users/Георгий/Downloads/Telegram Desktop/ChatExport_2026-10-29 (4)/"
S = (320, 173)
def g(p):
    return np.asarray(Image.open(p).convert("L").resize(S, Image.LANCZOS), float)
def corr(a, b):
    a = a - a.mean(); b = b - b.mean()
    return float((a * b).sum() / np.sqrt((a * a).sum() * (b * b).sum()))
out = []
for r in csv.DictReader(open("data/sancho-trades-skeleton.csv", encoding="utf-8")):
    if not r["photos"] or not r["tv_links"]: continue
    ids = sorted({l.rstrip("/").split("/")[-1] for l in r["tv_links"].split("|")})
    for ph in r["photos"].split("|"):
        a = g(D + ph)
        sc = sorted(((corr(a, g(f"data/sancho-tv/{i}.png")), i) for i in ids), reverse=True)
        out.append((r["trade_id"], ph, sc[0][1], round(sc[0][0], 3), round(sc[1][0], 3) if len(sc) > 1 else ""))
w = csv.writer(open("data/sancho-tv-pairs.csv", "w", encoding="utf-8", newline=""))
w.writerow(["trade_id", "photo", "best_snap", "corr", "second"]); w.writerows(out)
for o in out: print(*o)
