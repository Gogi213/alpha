#!/usr/bin/env python3
"""TK-291 (П-19 §3, §7 п.3): гейт min-лота при k = 1/4. По сделкам B1 (r-b/<мес>/<сутки>/<set>/rounds.csv) и instruments.csv
(min_order_qty, qty_step, min_notional_value): лот k·qty, округлённый вниз к qty_step, должен быть ≥ min_order_qty, а
k·qty·entry ≥ min_notional_value. Печатает по монетам: сделок, не прошедших, мин. лот в $ и в штуках.
tk291-minlot.py <r-b дом> <instruments.csv> [k=0.25]"""
import collections, csv, glob, math, os, sys

FORM = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
SET = "t-bid-btc4h-q1"
DROP = {"TRUMPUSDT", "TRXUSDT", "BCHUSDT"}
home, inst = sys.argv[1], sys.argv[2]
k = float(sys.argv[3]) if len(sys.argv) > 3 else 0.25
ins = {r["symbol"]: r for r in csv.DictReader(open(inst, encoding="utf-8"))}
st = collections.defaultdict(lambda: [0, 0, 0, 1e18, 1e18])  # n, fail_qty, fail_notional, min_usd, min_qty/minq
miss = collections.Counter()
for f in sorted(glob.glob(os.path.join(home, "20??-??", "20*", SET, "rounds.csv"))):
    lines = [l for l in open(f, encoding="utf-8") if not l.startswith("#")]
    for r in csv.DictReader(lines):
        if r["form"] != FORM or r["symbol"] in DROP:
            continue
        s = r["symbol"]
        if s not in ins:
            miss[s] += 1
            continue
        e = float(r.get("entry_vwap") or 0) or float(r["entry_px"])
        q = float(r["qty"]) * k
        step, mq, mn = float(ins[s]["qty_step"]), float(ins[s]["min_order_qty"]), float(ins[s]["min_notional_value"])
        qr = math.floor(q / step + 1e-9) * step
        a = st[s]
        a[0] += 1
        a[1] += qr < mq - 1e-12
        a[2] += qr * e < mn - 1e-12
        q1 = math.floor(float(r["qty"]) / step + 1e-9) * step
        if (qr < mq - 1e-12 or qr * e < mn - 1e-12) and not (q1 < mq - 1e-12 or q1 * e < mn - 1e-12):
            print("НОВЫЙ отказ", s, os.path.basename(os.path.dirname(os.path.dirname(f))), "lot_usd", round(q * e, 2),
                  "net_bps", r["net_bps"], "pnl_usd", round(q * e * float(r["net_bps"]) / 1e4, 4))
        a[3] = min(a[3], q * e)
        a[4] = min(a[4], q / mq)
tot = [0, 0, 0]
print(f"k={k}  sym n fail_qty fail_notional min_lot_usd min_qty/min_order_qty")
for s, a in sorted(st.items()):
    print(s, a[0], a[1], a[2], round(a[3], 2), round(a[4], 2))
    tot[0] += a[0]; tot[1] += a[1]; tot[2] += a[2]
print("ИТОГО сделок", tot[0], "не прошло qty", tot[1], "не прошло notional", tot[2], "монет", len(st), "нет в instruments:", dict(miss))
