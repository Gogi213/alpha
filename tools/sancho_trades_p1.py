"""TK-454: порядок событий по правилу П1 (протокол §2.4–2.6, §8) и сверка с заявленным исходом.
Запуск на calc: python3 sancho_trades_p1.py <trades.csv> <hand.csv> <text.csv> <claim.csv> <out.csv>.
Старт «стоп или цель1»: «план» — первая минута t>=t_post, где [L,H] накрывает entry (до конца цепочки); «отчёт/итог» —
первая минута накрытия entry в окне [t_pub_img−ТФ, t_post]; не найдено — «нельзя проверить: нет минуты входа».
Допуск ε=0 и ε1=медиана (H−L) за сутки до t_post. Минута, где касаются стоп и цель (в т.ч. минута входа) — неразрешимо."""
import csv, gzip, re, sys, bisect, statistics, os, datetime as dt
D = "/srv/alpha-sda/candles-1m/binance-um/"
TF = {"m": 60000, "h": 3600000, "d": 86400000, "w": 604800000}
def post_ms(s):
    m = re.match(r"(\d\d)\.(\d\d)\.(\d{4}) (\d\d):(\d\d)", s or "")
    d, mo, y, hh, mm = map(int, m.groups())
    return int((dt.datetime(y, mo, d, hh, mm, tzinfo=dt.timezone.utc) - dt.timedelta(hours=4)).timestamp() * 1000)
def tf_ms(s):
    m = re.search(r"\s(\d+)([mhdwMHDW])\b", s or "")
    return int(m.group(1)) * TF[m.group(2).lower()] if m else None
def load(sym, lo, hi):
    p = D + sym + ".csv.gz"
    if not os.path.exists(p): p = D + "1000" + sym + ".csv.gz"
    if not os.path.exists(p): return []
    out = []
    for l in gzip.open(p, "rt"):
        x = l.split(",")
        if x[0].isdigit() and lo <= int(x[0]) <= hi: out.append((int(x[0]), float(x[2]), float(x[3]), float(x[4])))
    return out
rd = lambda f: list(csv.DictReader(open(f, encoding="utf-8")))
tr, hand, text, claim = (rd(a) for a in sys.argv[1:5])
htf = {(h["trade_id"], h["image_file"]): tf_ms(h["chart_exchange_tf_market"]) for h in hand}
tx = {t["trade_id"]: t for t in text}
cl = {}
for c in claim: cl.setdefault(c["trade_id"], c)
cache = {}; out = []
rng = {}
for r in tr:
    if r["entry"] and r["trade_id"] in tx:
        a = post_ms(tx[r["trade_id"]]["t_post_gmt4"]); b = post_ms(tx[r["trade_id"]]["t_last_gmt4"])
        lo, hi = rng.get(r["symbol"], (a, b)); rng[r["symbol"]] = (min(lo, a), max(hi, b))
for r in tr:
    o = {"trade_id": r["trade_id"], "image_file": r["image_file"], "dir": r["dir"], "symbol": r["symbol"],
         "post_class": "", "claim_class": "", "entry_min": "", "p1": "", "match": ""}
    c = cl.get(r["trade_id"]); o["post_class"] = c["post_class"] if c else ""; o["claim_class"] = c["claim_class"] if c else ""
    if not r["entry"] or r["check"].startswith(("не извлечено", "нельзя", "ФЛАГ")):
        o["p1"] = r["check"]; o["match"] = "нельзя проверить: нет цены/флаг"; out.append(o); continue
    sym = r["symbol"]; d = r["dir"]; e, s, t1 = float(r["entry"]), float(r["stop"]), float(r["tp1"])
    cd = cache[sym] if sym in cache else cache.setdefault(sym, load(sym, rng[sym][0] - 3 * 86400000, rng[sym][1] + 121 * 86400000))
    if not cd: o["p1"] = "нельзя проверить: данные"; o["match"] = o["p1"]; out.append(o); continue
    ts = [x[0] for x in cd]; t = tx[r["trade_id"]]; tp, tl = post_ms(t["t_post_gmt4"]), post_ms(t["t_last_gmt4"])
    tf = htf.get((r["trade_id"], r["image_file"])) or 3600000
    plan = o["post_class"] == "план"
    i0 = bisect.bisect_left(ts, tp)
    day = cd[max(0, i0 - 1440):i0]
    eps1 = statistics.median(x[1] - x[2] for x in day) if day else 0.0
    res = []
    for eps in (0.0, eps1):
        if plan: lo, hi = tp, tl + 60000
        else: lo, hi = int(r["t_pub_utc_ms"]) - tf, tp + 60000
        a, b = bisect.bisect_left(ts, lo), bisect.bisect_left(ts, hi)
        j = next((k for k in range(a, b) if cd[k][2] - eps <= e <= cd[k][1] + eps), None)
        if j is None: res.append(("нет минуты входа", None)); continue
        ans = "ни то ни другое"; tt = None
        for k in range(j, min(len(cd), j + 120 * 1440)):
            _, h, l, _c = cd[k]
            hs = (h + eps >= s) if d == "short" else (l - eps <= s)
            ht = (l - eps <= t1) if d == "short" else (h + eps >= t1)
            if hs and ht: ans, tt = "неразрешимо", cd[k][0]; break
            if hs: ans, tt = "стоп", cd[k][0]; break
            if ht: ans, tt = "цель1", cd[k][0]; break
        res.append((ans, cd[j][0], tt))
    o["entry_min"] = "|".join(str(x[1]) for x in res)
    o["p1"] = "eps0:%s@%s; eps1(%.3g bps):%s@%s" % (res[0][0], res[0][2] if len(res[0]) > 2 else "", eps1 / cd[i0 - 1][3] * 1e4 if i0 else 0,
                                                    res[1][0], res[1][2] if len(res[1]) > 2 else "")
    cc = o["claim_class"]; r0, r1 = res[0][0], res[1][0]
    if "нет минуты входа" in (r0, r1): o["match"] = "нельзя проверить: нет минуты входа" if r0 == r1 else "зависит от допуска"
    elif "неразрешимо" in (r0, r1): o["match"] = "нельзя проверить: неразрешимо в минуте"
    elif not cc or cc.startswith(("неоднозначно", "открыта", "частичная")) or "БУ" in cc or "перенесён" in cc: o["match"] = "нельзя проверить: класс заявления не сводится к стоп/цель1"
    elif r0 != r1: o["match"] = "зависит от допуска"
    elif cc.startswith("цель"): o["match"] = "совпало" if r0 == "цель1" else "не совпало"
    elif cc.startswith("стоп"): o["match"] = "совпало" if r0 == "стоп" else "не совпало"
    else: o["match"] = "нельзя проверить: класс " + cc
    out.append(o)
w = csv.DictWriter(open(sys.argv[5], "w", newline="", encoding="utf-8"), fieldnames=list(out[0]))
w.writeheader(); w.writerows(out)
from collections import Counter
print(Counter(o["match"] for o in out))
