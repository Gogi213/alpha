#!/usr/bin/env python3
"""TK-379: все монеты Gate с оборотом 24ч >= 30k$ (по рынку), метки Bybit/Binance/OKX и дельта к уже скачанному (TK-372/375).
Использует tk372-gate-dl.py (get, STRIP, bybit_coins). Выход: --out csv (все, с метками), --lists каталог с list-{fut,spot}-d30.csv (дельта)."""
import argparse, csv, importlib.util, json, os, re, sys, urllib.request

here = os.path.dirname(os.path.abspath(__file__))
sp = importlib.util.spec_from_file_location("dl", os.path.join(here, "tk372-gate-dl.py")); dl = importlib.util.module_from_spec(sp); sp.loader.exec_module(dl)
STRIP, STABLE = dl.STRIP, dl.STABLE


def jget(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "alpha-research"}), timeout=40) as r:
        return json.load(r)


def bases(fn):
    try:
        s = fn(); return s
    except Exception as e:
        print("ОШИБКА источника:", fn.__name__, e, file=sys.stderr); return None


def binance():
    s = set()
    for u in ("https://api.binance.com/api/v3/exchangeInfo", "https://fapi.binance.com/fapi/v1/exchangeInfo"):
        for x in jget(u)["symbols"]:
            if x.get("status") == "TRADING": s.add(x["baseAsset"]); s.add(STRIP.sub("", x["baseAsset"]))
    return s


def okx():
    s = set()
    for t in ("SPOT", "SWAP"):
        for x in jget("https://www.okx.com/api/v5/public/instruments?instType=" + t)["data"]:
            if x.get("state") == "live": s.add(x["instId"].split("-")[0]); s.add(STRIP.sub("", x["instId"].split("-")[0]))
    return s


def has(base, st):
    if st is None: return ""
    return "1" if base in st or STRIP.sub("", base) in st else "0"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); ap.add_argument("--lists", required=True)
    ap.add_argument("--min-usd", type=float, default=30000); ap.add_argument("--have", nargs="+", required=True, help="csv symbol,market уже скачанных")
    a = ap.parse_args()
    bb, cnt = dl.bybit_coins(); bn = bases(binance); ox = bases(okx)
    have = set()
    for p in a.have:
        for r in csv.DictReader(open(p, encoding="utf-8-sig")): have.add((r["market"], r["symbol"]))
    cs = {c["name"]: c for c in dl.get("/api/v4/futures/usdt/contracts")}
    rows = []
    for t in dl.get("/api/v4/futures/usdt/tickers"):
        n = t["contract"]; c = cs.get(n)
        if not c or c.get("in_delisting") in (True, "true") or c.get("status", "trading") != "trading": continue
        rows.append([n, "фьюч", float(t["volume_24h_quote"]), t["volume_24h_quote"], "USDT", c["quanto_multiplier"], c.get("status", ""), n[:-5]])
    ps = {p["id"]: p for p in dl.get("/api/v4/spot/currency_pairs")}; tk = {t["currency_pair"]: t for t in dl.get("/api/v4/spot/tickers")}
    skip_q = 0
    for pid, p in ps.items():
        if p["trade_status"] != "tradable" or pid not in tk: continue
        q = p["quote"]; qv = float(tk[pid]["quote_volume"])
        if q in STABLE: usd = qv
        elif q + "_USDT" in tk: usd = qv * float(tk[q + "_USDT"]["last"])
        else: skip_q += 1; continue
        rows.append([pid, "спот", usd, tk[pid]["quote_volume"], q, "", p["trade_status"], p["base"]])
    rows = sorted((r for r in rows if r[2] >= a.min_usd), key=lambda r: -r[2])
    os.makedirs(a.lists, exist_ok=True)
    out = []; delta = {"фьюч": [], "спот": []}
    for r in rows:
        done = (r[1], r[0]) in have
        out.append([r[0], r[1], "%.2f" % r[2], r[3], r[4], has(r[7], bb), has(r[7], bn), has(r[7], ox), "1" if done else "0"])
        if not done: delta[r[1]].append(r)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["symbol", "market", "turnover24h_usd", "native_turnover", "native_unit", "on_bybit", "on_binance", "on_okx", "already_downloaded"]); w.writerows(out)
    hdr = ["gate", "market", "turnover24h_usd", "native_turnover", "native_unit", "gate_quanto_multiplier", "gate_trade_status"]
    for mk, name in (("фьюч", "fut"), ("спот", "spot")):
        with open("%s/list-%s-d30.csv" % (a.lists, name), "w", newline="") as f:
            w = csv.writer(f, lineterminator="\n"); w.writerow(hdr)
            w.writerows([[r[0], r[1], "%.2f" % r[2], r[3], r[4], r[5], r[6]] for r in delta[mk]])
    tot = {m: sum(1 for r in rows if r[1] == m) for m in ("фьюч", "спот")}
    print("Gate >= %g$: фьюч %d, спот %d; уже скачано: фьюч %d, спот %d; дельта: фьюч %d, спот %d; спот без USD-котировки %d" % (
        a.min_usd, tot["фьюч"], tot["спот"], tot["фьюч"] - len(delta["фьюч"]), tot["спот"] - len(delta["спот"]), len(delta["фьюч"]), len(delta["спот"]), skip_q))
    print("Bybit Trading %s; Binance %s; OKX %s" % (cnt, "—" if bn is None else len(bn), "—" if ox is None else len(ox)))
    lost = [k for k in have if k not in {(r[1], r[0]) for r in rows}]
    print("скачано, но сейчас < порога/нет в тикере: %d" % len(lost))


if __name__ == "__main__":
    main()
