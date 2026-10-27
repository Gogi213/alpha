#!/usr/bin/env python3
"""TK-425 (G-017, Д13): списки инструментов как отдаёт биржа (сырой JSON), даты листинга и таблица пар одной монеты.
  Bybit: /v5/market/instruments-info (linear Trading+Closed+PreLaunch, spot) — launchTime, baseCoin, quoteCoin, settleCoin, contractType.
  Gate : /futures/usdt/contracts (create_time, launch_time), /spot/currency_pairs (buy_start, sell_start) — что отдаёт API.
Выход: <root>/raw/*.json, <root>/listing.csv (биржа,рынок,инструмент,база,листинг_мс,источник,примечание),
<root>/pairs.csv (база, имена по 4 рынкам, вид связи: exact | mult (префикс 1000/10000/… в имени фьючерса) | single), <root>/disputed.csv.
Использование: tk425-meta.py <root>
"""
import csv
import json
import os
import re
import sys
import time
import urllib.request

root = sys.argv[1]
os.makedirs(root + "/raw", exist_ok=True)


def get(url):
    for a in range(8):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.load(r)
        except Exception:
            time.sleep(2 * (a + 1))
    raise RuntimeError(url)


def bybit(cat):
    out = []
    for st in ("Trading", "Closed", "PreLaunch") if cat == "linear" else (None,):
        cur = ""
        while True:
            u = "https://api.bybit.com/v5/market/instruments-info?category=%s&limit=1000" % cat
            u += ("&status=" + st) if st else ""
            u += ("&cursor=" + cur) if cur else ""
            d = get(u)
            if d.get("retCode") != 0:
                break
            out += d["result"]["list"]
            cur = d["result"].get("nextPageCursor", "")
            if not cur:
                break
    return out


bl, bs = bybit("linear"), bybit("spot")
gf = get("https://api.gateio.ws/api/v4/futures/usdt/contracts")
gs = get("https://api.gateio.ws/api/v4/spot/currency_pairs")
for n, d in (("bybit-linear", bl), ("bybit-spot", bs), ("gate-futures_usdt", gf), ("gate-spot", gs)):
    json.dump(d, open("%s/raw/%s.json" % (root, n), "w"))

rows = []  # биржа, рынок, инструмент, база, листинг_мс, источник, примечание
for x in bl:
    rows.append(("bybit", "linear", x["symbol"], x.get("baseCoin", ""), x.get("launchTime", ""), "launchTime",
                 "%s %s settle=%s" % (x.get("status", ""), x.get("contractType", ""), x.get("settleCoin", ""))))
for x in bs:
    rows.append(("bybit", "spot", x["symbol"], x.get("baseCoin", ""), x.get("launchTime", ""), "launchTime", x.get("status", "")))
for x in gf:
    ct = x.get("create_time")
    rows.append(("gate", "futures_usdt", x["name"], x["name"].split("_")[0], int(ct) * 1000 if ct else "", "create_time" if ct else "",
                 "" if ct else "нет"))
for x in gs:
    bt = x.get("buy_start")
    rows.append(("gate", "spot", x["id"], x.get("base", x["id"].split("_")[0]), int(bt) * 1000 if bt else "", "buy_start" if bt else "",
                 ("" if bt else "нет, ") + x.get("trade_status", "")))
with open(root + "/listing.csv", "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f)
    w.writerow(["биржа", "рынок", "инструмент", "база", "листинг_мс", "источник", "примечание"])
    w.writerows(rows)

MULT = re.compile(r"^(1000000|100000|10000|1000|100|10)(?=[A-Z])")


def norm(base):
    return MULT.sub("", base) if base else base


by = {}  # база -> {(биржа, рынок): [инструменты]}; Bybit linear не USDT-расчётный (USDC/PERP/инверсные) — отдельный ключ
for ex, mk, name, base, _, _, note in rows:
    key = ("bybit", "linear_other") if mk == "linear" and "settle=USDT" not in note else (ex, mk)
    by.setdefault(base, {}).setdefault(key, []).append(name)

pairs = []
for base, m in sorted(by.items()):
    pairs.append([base, norm(base), ";".join(m.get(("bybit", "linear"), [])), ";".join(m.get(("bybit", "spot"), [])),
                  ";".join(m.get(("gate", "futures_usdt"), [])), ";".join(m.get(("gate", "spot"), [])),
                  ";".join(m.get(("bybit", "linear_other"), []))])
# склейка по нормализованной базе: «1000BONK» (Bybit linear) ↔ «BONK» (спот/Gate)
merged = {}
for r in pairs:
    g = merged.setdefault(r[1], {"names": set(), "bl": [], "bs": [], "gf": [], "gs": [], "bo": []})
    g["names"].add(r[0])
    for k, v in zip(("bl", "bs", "gf", "gs", "bo"), r[2:]):
        if v:
            g[k] += v.split(";")
with open(root + "/pairs.csv", "w", encoding="utf-8", newline="") as f, open(root + "/disputed.csv", "w", encoding="utf-8", newline="") as fd:
    w, wd = csv.writer(f), csv.writer(fd)
    w.writerow(["база_норм", "bybit_linear", "bybit_spot", "gate_futures", "gate_spot", "bybit_linear_не_USDT", "связь", "имена_базы"])
    wd.writerow(["база_норм", "имена_базы", "bybit_linear", "bybit_spot", "gate_futures", "gate_spot", "причина"])
    for nb, g in sorted(merged.items()):
        n_mk = sum(bool(g[k]) for k in ("bl", "bs", "gf", "gs"))
        kind = "single" if n_mk < 2 else ("mult" if len(g["names"]) > 1 else "exact")
        w.writerow([nb, ";".join(g["bl"]), ";".join(g["bs"]), ";".join(g["gf"]), ";".join(g["gs"]), ";".join(g["bo"]), kind, ";".join(sorted(g["names"]))])
        why = []
        if len(g["names"]) > 1:
            why.append("разные имена базы (множитель 1000/10000…): проверить, что это один актив")
        if len(g["bl"]) > 1 or len(g["gf"]) > 1:
            why.append("несколько фьючерсных контрактов на базу")
        if why and n_mk >= 2:
            wd.writerow([nb, ";".join(sorted(g["names"])), ";".join(g["bl"]), ";".join(g["bs"]), ";".join(g["gf"]), ";".join(g["gs"]), "; ".join(why)])
print("bybit linear %d spot %d; gate fut %d spot %d; баз %d" % (len(bl), len(bs), len(gf), len(gs), len(merged)))
