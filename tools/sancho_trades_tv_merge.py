"""TK-454: строки по снимкам TV (handtv csv) -> формат trades/hand/t0/claim для sancho_trades_p1.py, слитые с прежними 33.
Запуск: python tools/sancho_trades_tv_merge.py <findings-dir> <out-dir>. Время: пояс графика -> GMT+4 (метка «~» отброшена);
tpub = время снимка; символ: .P/PERP/спот-тикер -> XXXUSDT (свечи binance-um); 1 строка на цепочку (первая с уровнями)."""
import csv, re, sys, datetime as dt
F, O = sys.argv[1] + "/", sys.argv[2] + "/"
S = "-2026-10-30.csv"
rd = lambda f: list(csv.DictReader(open(F + f + S, encoding="utf-8")))
def w(name, rows, cols):
    x = csv.DictWriter(open(O + name, "w", newline="", encoding="utf-8"), fieldnames=cols); x.writeheader(); x.writerows(rows)
tr, hand, t0, claim, text, tv, cltv = (rd(n) for n in ("sancho-trades", "sancho-trades-hand", "sancho-trades-t0", "sancho-trades-claim", "sancho-trades-text", "sancho-trades-handtv", "sancho-trades-claim-tv"))
ctv = {c["trade_id"]: c for c in cltv}  # ручной класс заявления цепочек TV (sancho_trades_claim_tv.py)
tx = {t["trade_id"]: t for t in text}
have = {r["trade_id"] for r in tr}
def parse(s, off):
    m = re.search(r"(\d\d)\.(\d\d)\.(\d{4})\D+(\d\d):(\d\d)", s or "")
    if not m: return None
    d, mo, y, hh, mm = map(int, m.groups())
    return dt.datetime(y, mo, d, hh, mm) + dt.timedelta(hours=4 - off)
num = lambda s: re.fullmatch(r"\d+(\.\d+)?", (s or "").strip()) is not None
seen = set(); nt = []; nh = []; n0 = []; nc = []
for r in tv:
    i = r["trade_id"]
    if i in have or i in seen: continue
    off = {"UTC+2": 2, "UTC+3": 3}.get(r["chart_tz"].strip(), 3)
    sym = re.match(r"([A-Z0-9]+?USDT)", r["chart_tf_market"]).group(1)
    snap = parse(r["snap_time_chart_tz"], off)
    ok = num(r["entry"]) and num(r["stop"]) and num(r["tp"])
    if not ok and not any(num(x["entry"]) for x in tv if x["trade_id"] == i): seen.add(i); nt.append({"trade_id": i, "image_file": r["tv_snap"], "dir": r["dir"], "symbol": sym, "t_pub_utc_ms": "", "entry": "", "stop": "", "tp1": "", "tps": "", "scale": "", "check": "нельзя: на снимках TV нет бокса позиции"}); continue
    if not ok: continue
    seen.add(i)
    pub = int((snap - dt.timedelta(hours=4)).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    nt.append({"trade_id": i, "image_file": r["tv_snap"], "dir": r["dir"], "symbol": sym, "t_pub_utc_ms": pub, "entry": r["entry"], "stop": r["stop"], "tp1": r["tp"], "tps": r["tp"], "scale": "TV " + r["chart_tz"].strip(), "check": "TV"})
    nh.append({"trade_id": i, "image_file": r["tv_snap"], "source": "tv", "chart_exchange_tf_market": r["chart_tf_market"]})
    b = parse(r["box_left_chart_tz"], off)
    if b: n0.append({"trade_id": i, "image_file": r["tv_snap"], "box_left_gmt4": b.strftime("%d.%m.%Y %H:%M"), "edge_hi_gmt4": "", "tf": "", "chart_tz": r["chart_tz"], "read": "TV handtv"})
    c = ctv[i]; nc.append({"trade_id": i, "post_class": c["post_class"], "claim_class": c["claim_class"], "claim_R": c["claim_R"], "quote": c["quote"]})
cols = lambda a, b: list(a[0]) if a else list(b[0])
w("trades.csv", tr + nt, list(tr[0]) + [c for c in nt[0] if c not in tr[0]])
w("hand.csv", hand + nh, list(hand[0]))
w("t0.csv", t0 + n0, list(t0[0]))
w("claim.csv", claim + nc, list(claim[0]))
w("text.csv", text, list(text[0]))
print(len(nt), "новых строк trades;", len(nh), "с уровнями;", len(n0), "с краем бокса")
