#!/usr/bin/env python3
"""TK-372 (G-016): сделки Gate за 15–21.10.2026 (UTC) по монетам, общим с юниверсом Bybit (>50k$/сут).
Стенд: stdlib, python3. Подкоманды:
  list  --ticks <bybit-ticks.csv> --out <каталог>      → list-fut.csv / list-spot.csv (карта Bybit→Gate)
  run   --root <каталог> [--workers 4] [--rps 10]       → raw/<market>/<SYM>/<date>.csv.gz + log/dl.tsv (докачка: готовое пропускается)
  check --root <каталог>                                → log/check.tsv: сумма объёма сделок против свечей 1м за сутки
API: REST futures/usdt/trades, spot/trades (from/to в секундах, [from,to) — правая граница исключена; ответ — от новых к
старым, ≤1000 строк). Пагинация вниз по времени: следующее to = (минимальная секунда страницы)+1, дубли по id; секунда
плотнее 1000 строк — offset. Поля как отдаёт биржа (строками, без пересчёта); id сквозные по контракту → полнота = число
строк суток == max-min+1. Нет `side` у фьючерсов — знак size (как отдаёт биржа)."""
import argparse, csv, gzip, http.client, io, json, os, sys, threading, time, calendar, re, collections

HOST = "api.gateio.ws"
DAYS = ["2026-10-15", "2026-10-16", "2026-10-17", "2026-10-18", "2026-10-19", "2026-10-20", "2026-10-21"]
COLS = {"fut": ["id", "contract", "create_time", "create_time_ms", "size", "price"],
        "spot": ["id", "create_time", "create_time_ms", "currency_pair", "side", "amount", "price", "sequence_id"]}
URL = {"fut": "/api/v4/futures/usdt/trades?contract=%s", "spot": "/api/v4/spot/trades?currency_pair=%s"}
CANDLE = {"fut": "/api/v4/futures/usdt/candlesticks?contract=%s&interval=1m", "spot": "/api/v4/spot/candlesticks?currency_pair=%s&interval=1m"}
tl = threading.local()
G = {"pause_until": 0.0, "last": 0.0, "rps": 10.0, "lock": threading.Lock(), "n429": 0, "req": 0, "e403": 0}


def day0(d):
    y, m, dd = map(int, d.split("-")); return calendar.timegm((y, m, dd, 0, 0, 0))


def get(path):
    """GET с общим лимитером, бэкоффом на 429/5xx, keep-alive на поток. Возврат — разобранный JSON (числа строками)."""
    for k in range(12):
        with G["lock"]:
            now = time.time(); w = max(G["pause_until"] - now, G["last"] + 1.0 / G["rps"] - now, 0)
            G["last"] = now + w; G["req"] += 1
        if w: time.sleep(w)
        try:
            c = getattr(tl, "c", None)
            if c is None:
                c = tl.c = http.client.HTTPSConnection(HOST, timeout=40)
            c.request("GET", path, headers={"User-Agent": "alpha-research", "Accept-Encoding": "gzip"})
            r = c.getresponse(); b = r.read()
            if r.getheader("Content-Encoding") == "gzip": b = gzip.decompress(b)
            if r.status == 200:
                return json.loads(b, parse_float=str, parse_int=str)
            if r.status == 429:
                rs = r.getheader("X-Gate-RateLimit-Reset-Timestamp"); t = float(rs) - time.time() + 0.5 if rs else 10
                with G["lock"]:
                    G["n429"] += 1; G["pause_until"] = max(G["pause_until"], time.time() + min(max(t, 2), 60))
                continue
            if r.status == 403:
                with G["lock"]:
                    G["e403"] += 1; G["pause_until"] = max(G["pause_until"], time.time() + 300)
                if G["e403"] > 5: raise SystemExit("403 x5: бан IP, остановка")
                continue
            if r.status >= 500:
                tl.c = None; time.sleep(min(2 ** k, 60)); continue
            raise RuntimeError("HTTP %d %s" % (r.status, b[:200].decode("utf8", "replace")))
        except (OSError, http.client.HTTPException):
            tl.c = None; time.sleep(min(2 ** k, 60))
    raise RuntimeError("retries exhausted " + path)


def sec(r):
    return int(r["create_time"].split(".")[0])


def fetch_day(mk, sym, d):
    """Все сделки суток [d 00:00, d+1 00:00) UTC; возврат (rows по возрастанию id, число запросов)."""
    a = day0(d); b = a + 86400; base = URL[mk] % sym; seen = {}; hi = b; nreq = 0
    while True:
        rows = get("%s&from=%d&to=%d&limit=1000" % (base, a, hi)); nreq += 1
        new = 0
        for r in rows:
            if r["id"] not in seen: seen[r["id"]] = r; new += 1
        if len(rows) < 1000: break
        s = min(sec(r) for r in rows)
        if new == 0:  # секунда s плотнее страницы → offset по [s, s+1)
            off = 0
            while True:
                p = get("%s&from=%d&to=%d&limit=1000&offset=%d" % (base, s, s + 1, off)); nreq += 1
                for r in p: seen.setdefault(r["id"], r)
                if len(p) < 1000: break
                off += 1000
            hi = s
        else:
            hi = s + 1
        if hi <= a: break
    return sorted(seen.values(), key=lambda r: int(r["id"])), nreq


def write_day(path, mk, rows):
    cols = COLS[mk]; tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf8", newline="", compresslevel=6) as f:
        w = csv.writer(f, lineterminator="\n"); w.writerow(cols + ["_extra"])
        for r in rows:
            ex = {k: v for k, v in r.items() if k not in cols}
            w.writerow([r.get(c, "") for c in cols] + [json.dumps(ex, separators=(",", ":")) if ex else ""])
    os.replace(tmp, path)


def cmd_list(a):
    o = a.out; os.makedirs(o, exist_ok=True)
    bb = list(csv.DictReader(open(a.ticks, encoding="utf-8-sig"), delimiter=";"))
    bb = [x for x in bb if float(x["turnover24h_usd"]) > 50000]
    cs = get("/api/v4/futures/usdt/contracts"); names = {c["name"]: c for c in cs}
    out = []
    for x in bb:
        if x["market"] != "фьюч" or not x["symbol"].endswith("USDT"): continue
        b = x["symbol"][:-4]; st = re.sub(r"^(1000000|100000|10000|1000|100)(?=[A-Z])", "", b)
        for cand, how in ((b, "exact"), (st, "bybit-prefixed"), ("1000" + b, "gate-1000"), ("1000000" + b, "gate-1000000")):
            if cand + "_USDT" in names:
                c = names[cand + "_USDT"]; out.append([cand + "_USDT", x["symbol"], how, x["turnover24h_usd"], c["quanto_multiplier"], c["in_delisting"]]); break
    out.sort(key=lambda r: -float(r[3]))
    with open(o + "/list-fut.csv", "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n"); w.writerow(["gate", "bybit", "match", "bybit_turnover24h_usd", "gate_quanto_multiplier", "gate_in_delisting"]); w.writerows(out)
    ps = {p["id"]: p for p in get("/api/v4/spot/currency_pairs")}; byid = {p["base"] + p["quote"]: p for p in ps.values()}
    sp = []
    for x in bb:
        if x["market"] != "спот" or x["symbol"] not in byid: continue
        p = byid[x["symbol"]]; sp.append([p["id"], x["symbol"], p["trade_status"], x["turnover24h_usd"]])
    sp.sort(key=lambda r: -float(r[3]))
    with open(o + "/list-spot.csv", "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n"); w.writerow(["gate", "bybit", "gate_trade_status", "bybit_turnover24h_usd"]); w.writerows(sp)
    nf = sum(1 for x in bb if x["market"] == "фьюч"); ns = sum(1 for x in bb if x["market"] == "спот")
    print("fut: Bybit>50k %d, на Gate %d; spot: Bybit>50k %d, на Gate %d (tradable %d)" % (nf, len(out), ns, len(sp), sum(1 for r in sp if r[2] == "tradable")))


def units(root, days=None):
    u = []
    for mk in ("spot", "fut"):  # спот первым: история только 30 дн.
        p = "%s/list-%s.csv" % (root, mk)
        if not os.path.exists(p): continue
        for r in csv.DictReader(open(p)):
            if mk == "spot" and r["gate_trade_status"] != "tradable": continue
            for d in (days or DAYS): u.append((mk, r["gate"], d))
    return u


def cmd_run(a):
    root = a.root; G["rps"] = a.rps; os.makedirs(root + "/log", exist_ok=True)
    us = [x for x in units(root, a.days and a.days.split(",")) if not os.path.exists("%s/raw/%s/%s/%s.csv.gz" % (root, x[0], x[1], x[2]))]
    lock = threading.Lock(); it = iter(us); st = {"done": 0, "rows": 0}; t0 = time.time()
    logf = open(root + "/log/dl.tsv", "a", buffering=1)
    print("к скачиванию: %d монето-суток" % len(us), flush=True)

    def work():
        while True:
            with lock: x = next(it, None)
            if x is None: return
            mk, sym, d = x; t = time.time()
            try:
                rows, nreq = fetch_day(mk, sym, d)
                ids = [int(r["id"]) for r in rows]
                gaps = (ids[-1] - ids[0] + 1 - len(ids)) if ids else 0
                os.makedirs("%s/raw/%s/%s" % (root, mk, sym), exist_ok=True)
                write_day("%s/raw/%s/%s/%s.csv.gz" % (root, mk, sym, d), mk, rows)
                line = [mk, sym, d, "ok", len(rows), nreq, ids[0] if ids else "", ids[-1] if ids else "", gaps, "%.1f" % (time.time() - t)]
            except SystemExit: raise
            except Exception as e:
                line = [mk, sym, d, "fail:" + str(e)[:150].replace("\t", " "), 0, 0, "", "", "", "%.1f" % (time.time() - t)]
            with lock:
                logf.write("\t".join(map(str, line)) + "\n"); st["done"] += 1; st["rows"] += int(line[4])
                if st["done"] % 50 == 0:
                    print("%.0f с: %d/%d, строк %d, запросов %d, 429: %d" % (time.time() - t0, st["done"], len(us), st["rows"], G["req"], G["n429"]), flush=True)
    th = [threading.Thread(target=work) for _ in range(a.workers)]
    [t.start() for t in th]; [t.join() for t in th]
    print("готово: %.0f с, монето-суток %d, строк %d, запросов %d, 429: %d" % (time.time() - t0, st["done"], st["rows"], G["req"], G["n429"]), flush=True)


def cmd_check(a):
    """Сумма объёма сделок суток против суммы свечей 1м (fut: v в контрактах = Σ|size|; spot: base-объём = Σ amount)."""
    root = a.root; G["rps"] = a.rps; out = open(root + "/log/check.tsv", "a", buffering=1)
    us = []
    for x in units(root, a.days and a.days.split(",")):
        if os.path.exists("%s/raw/%s/%s/%s.csv.gz" % (root, *x[:1], x[1], x[2])) and (not a.only or x[1] in a.only.split(",")): us.append(x)
    lock = threading.Lock(); it = iter(us)

    def work():
        while True:
            with lock: x = next(it, None)
            if x is None: return
            mk, sym, d = x; t0 = day0(d)
            try:
                tr = 0.0
                with gzip.open("%s/raw/%s/%s/%s.csv.gz" % (root, mk, sym, d), "rt", newline="") as f:
                    for r in csv.DictReader(f): tr += abs(float(r["size"])) if mk == "fut" else float(r["amount"])
                cd = 0.0
                for k in range(2):  # свечи ≤ 1000 точек: сутки двумя половинами
                    p = "%s&from=%d&to=%d" % (CANDLE[mk] % sym, t0 + k * 43200, t0 + (k + 1) * 43200 - 60)  # limit с from/to фьючам нельзя (400)
                    for c in get(p): cd += float(c["v"]) if mk == "fut" else float(c[6])
                line = [mk, sym, d, "%.8g" % tr, "%.8g" % cd, "%.6f" % ((tr - cd) / cd if cd else 0.0)]
            except SystemExit: raise
            except Exception as e: line = [mk, sym, d, "", "", "fail:" + str(e)[:100]]
            with lock: out.write("\t".join(line) + "\n")
    th = [threading.Thread(target=work) for _ in range(a.workers)]
    [t.start() for t in th]; [t.join() for t in th]


if __name__ == "__main__":
    p = argparse.ArgumentParser(); s = p.add_subparsers(dest="c", required=True)
    l = s.add_parser("list"); l.add_argument("--ticks", required=True); l.add_argument("--out", required=True)
    for n in ("run", "check"):
        r = s.add_parser(n); r.add_argument("--root", required=True); r.add_argument("--workers", type=int, default=4); r.add_argument("--rps", type=float, default=10); r.add_argument("--days", default="")
    s.choices["check"].add_argument("--only", default="")
    a = p.parse_args(); {"list": cmd_list, "run": cmd_run, "check": cmd_check}[a.c](a)
