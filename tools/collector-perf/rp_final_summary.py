#!/usr/bin/env python3
"""Таблица «повтор записанных кадров»: прежний (base3) против финального (final5): медиана ЦП писателя и диска на Мкадр по 5 проходам, хеш разжатого содержимого (должен совпасть
между проходами и бинарями), у BingX — разжатие нс/кадр. Вход: каталог с summary-base3.txt и summary-final5.txt; вывод — markdown-таблица."""
import re
import statistics
import sys
from collections import defaultdict

d = sys.argv[1]
data = {}
for b in ("base3", "final5"):
    sh = defaultdict(lambda: {"cpu": [], "fnv": set(), "gz": [], "parse": [], "frames": 0})
    for ln in open(f"{d}/summary-{b}.txt", encoding="utf-8"):
        m = re.match(r"(\w+) (\w+) \| (.*)", ln.strip())
        if not m:
            continue
        key, rest = f"{m.group(1)} {m.group(2)}", m.group(3)
        if (x := re.search(r"=> ([\d.]+) CPU-s per Mframe.*fnv ([0-9a-f]+)", rest)):
            sh[key]["cpu"].append(float(x.group(1)))
            sh[key]["fnv"].add(x.group(2))
        if (x := re.search(r"gunzip.*?: (\d+) ns/frame", rest)):
            sh[key]["gz"].append(int(x.group(1)))
        if (x := re.search(r"parse only: ([\d.]+) ns/frame", rest)):
            sh[key]["parse"].append(float(x.group(1)))
        if (x := re.search(r"replay \w+ shard \w+: (\d+) frames", rest)):
            sh[key]["frames"] = int(x.group(1))
    data[b] = sh
print("| шард | кадров | писатель+диск, ЦП-с/Мкадр: старый | новый | Δ | хеш разжатого (все проходы, оба бинаря) | разжатие BingX, нс/кадр: старый → новый |")
print("|---|---|---|---|---|---|---|")
tot_o = tot_n = 0.0
for key in sorted(data["base3"]):
    o, n = data["base3"][key], data["final5"].get(key)
    if not n or not o["cpu"] or not n["cpu"]:
        continue
    mo, mn = statistics.median(o["cpu"]), statistics.median(n["cpu"])
    same = len(o["fnv"] | n["fnv"]) == 1
    gz = f"{statistics.median(o['gz']):.0f} → {statistics.median(n['gz']):.0f}" if o["gz"] and n["gz"] else ""
    print(f"| {key} | {o['frames']:,} | {mo:.2f} | {mn:.2f} | {100 * (mn - mo) / mo:+.0f} % | {'совпал' if same else 'РАЗНЫЕ'} {sorted(o['fnv'] | n['fnv'])[0] if same else ''} | {gz} |")
    tot_o += mo * o["frames"]
    tot_n += mn * n["frames"]
print(f"\nИтого по кадрам (взвешено числом кадров): {tot_o / 1e6:.2f} → {tot_n / 1e6:.2f} ЦП-с ({100 * (tot_n - tot_o) / tot_o:+.0f} %)")
