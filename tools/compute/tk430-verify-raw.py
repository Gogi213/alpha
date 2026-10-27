#!/usr/bin/env python3
"""TK-430: проверка сырых месячных архивов Gate fut в окне 30.09 07:58–08:03 UTC.
Использование: tk430-verify-raw.py <каталог raw с ИНСТР.csv.gz> <каталог свечей gate-futures_usdt> <инструмент>...
По каждому: размер, sha256, gzip-целостность, строк; строки окна с ценой ±5 % от медианы окна (дословно); h/l минуты 08:00 из сырья
против свечи calc; те из этих строк, что совпали по (время, цена, |размер|) с Gate REST liq_orders (если API доступен).
"""
import gzip
import hashlib
import json
import os
import sys
import urllib.request

T0, T1 = 1790755080, 1790755380  # 07:58:00 .. 08:03:00
MIN0 = 1790755200  # 08:00:00


def liq(contract):
    u = "https://api.gateio.ws/api/v4/futures/usdt/liq_orders?contract=%s&from=%d&to=%d&limit=1000" % (contract, T0, T1)
    try:
        with urllib.request.urlopen(u, timeout=30) as r:
            return json.load(r)
    except Exception as e:
        print("  liq_orders: ошибка", e)
        return None


raw, cand = sys.argv[1], sys.argv[2]
for c in sys.argv[3:]:
    fn = os.path.join(raw, c + ".csv.gz")
    sz = os.path.getsize(fn)
    h = hashlib.sha256()
    with open(fn, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    rows, win, m0 = 0, [], []
    ok = True
    try:
        with gzip.open(fn, "rt") as f:
            for line in f:
                rows += 1
                p = line.rstrip("\n").split(",")
                if p[0] == "timestamp" or not p[0][0].isdigit():
                    continue
                ts = float(p[0])
                if T0 <= ts < T1:
                    win.append((line.rstrip("\n"), ts, float(p[2]), float(p[3])))
                if MIN0 <= ts < MIN0 + 60:
                    m0.append(float(p[2]))
    except Exception as e:
        ok = False
        print("  gzip ошибка", e)
    print("== %s  %d Б  sha256 %s  gzip %s  строк %d" % (c, sz, h.hexdigest(), "ok" if ok else "ОШИБКА", rows))
    if not win:
        print("  окно пусто")
        continue
    ps = sorted(w[2] for w in win)
    med = ps[len(ps) // 2]
    out = [w for w in win if abs(w[2] / med - 1) > 0.05]
    print("  окно: %d сделок, медиана %g, вне ±5 %%: %d" % (len(win), med, len(out)))
    for w in out[:8]:
        print("   ", w[0])
    lq = liq(c)
    if lq is not None:
        keys = {(int(x["time"]), float(x["fill_price"]), abs(int(x["size"]))) for x in lq}
        hit = [w for w in out if (int(w[1]), w[2], abs(int(w[3]))) in keys]
        print("  liq_orders в окне: %d; из строк вне ±5 %% совпало с liq_orders: %d из %d" % (len(lq), len(hit), len(out)))
    if m0:
        line = None
        with gzip.open(os.path.join(cand, c + ".csv.gz"), "rt") as f:
            for l in f:
                if l.startswith("1790755200000,"):
                    line = l.rstrip("\n")
                    break
        p = line.split(",") if line else None
        if p:
            print("  минута 08:00: сырьё h=%g l=%g n=%d | свеча calc h=%s l=%s n=%s %s" % (
                max(m0), min(m0), len(m0), p[2], p[3], p[7],
                "РАВНО" if abs(max(m0) - float(p[2])) < 1e-12 and abs(min(m0) - float(p[3])) < 1e-12 and len(m0) == int(p[7]) else "РАЗНО"))
