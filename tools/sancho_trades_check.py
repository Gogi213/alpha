"""TK-454: нормализация цен hand-csv (масштаб по минутке на время снимка) и порядок событий по минуткам Binance UM.
Запуск на calc: python3 sancho_trades_check.py <hand.csv> <out.csv>. Описательно: стоп/цель1 первыми от минуты снимка
(ε=0 и ε1=медиана (H-L) суток снимка); сравнение с заявленным исходом — отдельным шагом (текст постов)."""
import csv, gzip, re, sys, bisect, statistics, os, datetime as dt
D = "/srv/alpha-sda/candles-1m/binance-um/"
H = 360 * 1440 * 60000
def utc_ms(s):
    m = re.match(r"(\d\d)\.(\d\d)\.(\d{4}) (\d\d):(\d\d)", s or "")
    if not m: return None
    d, mo, y, hh, mm = map(int, m.groups())
    k = re.search(r"UTC\+(\d)", s)
    arrow = re.search(r"UTC\+\d\s*→\s*(\d\d):(\d\d) GMT\+4", s)
    base = dt.datetime(y, mo, d, hh, mm, tzinfo=dt.timezone.utc)
    if arrow: off = int(k.group(1))                      # формат A: первое время — местное UTC+k
    else: off = 4                                         # формат B: первое время — GMT+4
    return int((base - dt.timedelta(hours=off)).timestamp() * 1000)
NUM = re.compile(r"^\s*(\d[\d,]*\.?\d*)")
def num(seg):
    m = NUM.match(seg)
    if not m: return None
    t = m.group(1)
    return float(t.replace(",", "")) if t.count(".") <= 1 else None
def nums(s):
    s = re.sub(r"\([^)]*\)", "", s or "")
    return [x for x in (num(p) for p in s.split(" / ")) if x is not None]
def sym_of(s):
    t = (s or "").split()[0] if s else ""
    t = t.replace("(E)", "E").replace(".P", "")
    return t if t.endswith("USDT") else None
def load(sym):
    p = D + sym + ".csv.gz"
    if not os.path.exists(p): return None
    r = [l.split(",") for l in gzip.open(p, "rt").read().split("\n")[1:] if l]
    return [(int(x[0]), float(x[2]), float(x[3]), float(x[4])) for x in r]
out = []
cache = {}
for r in csv.DictReader(open(sys.argv[1], encoding="utf-8")):
    o = {"trade_id": r["trade_id"], "image_file": r["image_file"], "dir": "", "symbol": "", "t_pub_utc_ms": "",
         "entry": "", "stop": "", "tp1": "", "tps": "", "scale": "", "check": ""}
    sym = sym_of(r["chart_exchange_tf_market"]); d = r["dir"].split()[0] if r["dir"] else ""
    e = nums(r["entry"]); s = nums(r["stop"]); tp = nums(r["tp_list"]); t0 = utc_ms(r["t_pub_img_gmt4"])
    o.update(dir=d, symbol=sym or "")
    if not (sym and d in ("long", "short") and e and s and tp and t0):
        o["check"] = "не извлечено: нет символа/направления/уровня/времени"; out.append(o); continue
    o["t_pub_utc_ms"] = t0
    base = "база=Binance-перп" if r["chart_exchange_tf_market"].split()[0].endswith(".P") and "BINANCE" in r["chart_exchange_tf_market"] else "база≠(спот/индекс/иная биржа: %s)" % " ".join(r["chart_exchange_tf_market"].split()[:3])
    c = cache.get(sym)
    if c is None:
        c = cache[sym] = load(sym) or load("1000" + sym[:-4].replace("USDT", "") + "USDT") or []
    mult = 1
    if not os.path.exists(D + sym + ".csv.gz") and os.path.exists(D + "1000" + sym + ".csv.gz"): sym = "1000" + sym; mult = 1000
    if not c: o["check"] = "нельзя проверить: данные"; out.append(o); continue
    ts = [x[0] for x in c]; i = bisect.bisect_right(ts, t0) - 1
    if i < 0 or t0 - ts[i] > 3600000: o["check"] = "нельзя проверить: нет минутки на время снимка"; out.append(o); continue
    close = c[i][3]; sc = None
    for f in (1, 1e-3, 1e-6):
        if 0.5 <= e[0] * f * mult / close <= 2: sc = f; break
    if sc is None: o["check"] = "ФЛАГ: порядок цены entry≠минутка (close %s, entry %s)" % (close, e[0]); out.append(o); continue
    for k, v in (("entry", e[0]), ("stop", s[0]), ("tp1", tp[0])): o[k] = round(v * sc * mult, 10)
    o["tps"] = "|".join(str(round(v * sc * mult, 10)) for v in tp)
    o["scale"] = ("×%g " % sc if sc != 1 else "×1 ") + ("(уровни ×1000: инструмент 1000-множитель) " if mult != 1 else "") + base
    ent, stp, t1 = o["entry"], o["stop"], o["tp1"]
    day = [x for x in c if t0 - 86400000 <= x[0] <= t0]
    eps1 = statistics.median(x[1] - x[2] for x in day) if day else 0.0
    ok_order = (d == "long" and stp < ent < t1 * 1.0000001) or (d == "short" and t1 < ent < stp)
    if not ok_order: o["check"] = "ФЛАГ: порядок stop/entry/tp1 не соответствует направлению"; out.append(o); continue
    res = []
    for eps in (0.0, eps1):
        st = tg = None
        for ts_, h, l, cl in c[i:i + 120 * 1440]:
            hs = (h + eps >= stp) if d == "short" else (l - eps <= stp)
            ht = (l - eps <= t1) if d == "short" else (h + eps >= t1)
            if hs and ht: res.append("неразрешимо@%s" % ts_); break
            if hs: res.append(("стоп@%s" if ts_ > t0 else "стоп уже на минуте снимка@%s") % ts_); break
            if ht: res.append(("цель1@%s" if ts_ > t0 else "цель1 уже на минуте снимка@%s") % ts_); break
        else: res.append("ни то ни другое за 120 сут/до конца данных")
    o["check"] = "eps0:%s; eps1(%.3g bps):%s" % (res[0], eps1 / close * 1e4, res[1])
    out.append(o)
w = csv.DictWriter(open(sys.argv[2], "w", newline="", encoding="utf-8"), fieldnames=list(out[0]))
w.writeheader(); w.writerows(out)
print(len(out), sum(1 for o in out if o["check"].startswith(("eps",))), "проверено;", "флаги:", [ (o["trade_id"],o["check"][:60]) for o in out if not o["check"].startswith("eps")])
