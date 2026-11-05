#!/usr/bin/env python3
"""Hyperliquid: свечи и история фандинга ВСЕХ перпетуалов (основной DEX + билдерские HIP-3 DEX: акции, ETF, индексы, металлы, нефть, валюта…)
за окно. Только публичные данные (POST https://api.hyperliquid.xyz/info), ключей нет.

Источники (замерено 05.11.2026 с VPS София):
  * контракты   {"type":"perpDexs"} + {"type":"allPerpMetas"} (все DEX одним ответом; у каждого universe: name, szDecimals, maxLeverage,
                isDelisted, collateralToken; имя билдерских — "<dex>:<тикер>", напр. "xyz:AAPL") + {"type":"spotMeta"} (индекс токена -> имя
                залога: 0=USDC, 235=USDE, 268=USDT0, 360=USDH) + {"type":"perpCategories"} (категория: stocks/indices/commodities/fx/crypto/
                preipo/rates; отдаётся ТОЛЬКО для живых билдерских контрактов, для основного DEX и снятых — нет => эвристика по тикеру);
  * свечи       {"type":"candleSnapshot","req":{coin,interval,startTime,endTime}} — по возрастанию t; endTime ВКЛЮЧИТЕЛЬНО (по t открытия);
                отдаёт не более ~5000 ПОСЛЕДНИХ свечей интервала в [startTime,endTime] (у снятых контрактов «последних» — от их последней
                свечи, а не от «сейчас»). Поэтому 1m доступны только за последние ≈5000 минут (≈3,5 суток) -> каталог candles/ (окно НЕПОЛНОЕ),
                а за всё окно берётся 15m (5000 x 15 мин ≈ 52 суток) -> каталог candles15/ (t_open_ms кратно 900000);
  * фандинг     {"type":"fundingHistory","coin","startTime","endTime"} — по возрастанию, до 500 записей/запрос, ставка ПОЧАСОВАЯ
                (interval_h=1; поле fundingRate, поле premium в формат не входит); у снятых контрактов история продолжается нулями
                -> для контрактов без свечей в окне фандинг не качается.
Единицы: v — в базовых единицах «как торгуется» (BTC — монеты, xyz:AAPL — акции; у k-контрактов kPEPE/kBONK/… единица = 1000 токенов,
цена за 1000 — так же отдаёт биржа), quote_v биржа НЕ отдаёт -> пусто. Свечи не закрытые (t + интервал > now) не пишутся.
Дыры (интервалы без сделок) не дорисовываются.
Результат (формат xdl_common): <out>/hyperliquid/{candles15,candles,funding,instruments.csv,_state,_logs}.
Лимит API (замер): общий на IP, «корзина» с пополнением ~20 ед. веса/с; вес зависит от числа возвращённых записей. Скрипт держит общий
ограничитель --rate (запросов/с), при 429 — общая пауза и автоматическое замедление, с возвратом скорости после серии успехов.
"""
import os, sys, json, time, threading, urllib.request, urllib.error, random

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xdl_common as X

EX = "hyperliquid"
API = "https://api.hyperliquid.xyz/info"
STEP = {"15m": 900_000, "1m": 60_000}
DIRNAME = {"15m": "candles15", "1m": "candles"}
DEPTH = 5002           # сколько последних свечей интервала реально отдаёт API (замер: 5000..5002)
CHUNK = 4990           # шаг по времени, если окно длиннее глубины (вперёд кусками)

# --- классификация --------------------------------------------------------------------------------------------------------------
METAL = {"GOLD", "SILVER", "PLATINUM", "PALLADIUM", "COPPER", "ALUMINIUM"}
ENERGY = {"OIL", "WTI", "BRENTOIL", "CL", "NATGAS", "GAS", "HO", "USOIL", "TTF"}
AGRI = {"CORN", "WHEAT", "SOY"}
ETF = {"URNM", "EWY", "EWJ", "EWZ", "EWT", "KORU", "XBI", "TLT", "SMH", "SOXL", "MUU", "XLE", "MAGS", "GDX", "IGV", "KWEB"}
FX = {"EUR", "JPY", "GBP", "KRW", "DXY"}
INDEX = {"USA500", "USA100", "US500", "SP500", "XYZ100", "JPN225", "JP225", "KR200", "NIFTY", "IBOV", "USTECH", "SMALL2000", "USENERGY",
         "SEMI", "GLDMINE", "BVIV", "VIX", "VOL", "MAG7"}
RATES = {"USBOND", "2Y", "10Y", "30Y"}
CRYPTO_INDEX = {"TOTAL2", "OTHERS", "BTCD"}
STOCKS = {"TSLA", "NVDA", "HOOD", "GOOGL", "INTC", "AMZN", "MSFT", "META", "AAPL", "BABA", "MU", "PLTR", "XIAOMI", "BMNR", "RTX", "TENCENT",
          "NAVER", "CAR", "SNAP", "TTWO", "MELI", "STX", "ACN", "COIN", "CRCL", "IONQ", "NBIS", "SNDK", "GPRO", "TCNT", "DRAM"}
CAT_CLASS = {"stocks": "equity", "stock": "equity", "indices": "index", "fx": "fx", "FX": "fx", "crypto": "crypto"}


def classify(name, cat):
    """-> (class, class_src, note). cat — категория из perpCategories или None."""
    dex, _, tick = name.rpartition(":")
    note = []
    if tick in CRYPTO_INDEX and dex:
        note.append("крипто-индекс (капитализация/доминация), не отдельный актив")
    if cat:
        src = "hl:perpCategories=" + cat
        if cat == "commodities":
            if tick in METAL:
                return "metal", src + "+ticker_map", "; ".join(note)
            if tick in ENERGY:
                return "energy", src + "+ticker_map", "; ".join(note)
            return "commodity", src + ("+ticker_map" if tick in AGRI else ""), "; ".join(note)
        if cat in ("stocks", "stock") and tick in ETF:
            return "etf", src + "+ticker_map", "; ".join(note)
        if cat in ("stocks", "stock") and tick in RATES:
            return "other", src + "+ticker_map", "; ".join(note + ["облигации/ставки, не акция"])
        if cat == "indices" and tick in ("GOLDJM", "SILVERJM"):
            note.append("название похоже на корзину компаний-добытчиков, не на металл")
        if cat == "crypto" and tick == "USDE":
            note.append("стейблкоин-контракт")
        if cat in CAT_CLASS:
            return CAT_CLASS[cat], src, "; ".join(note)
        if cat == "preipo":
            return "other", src, "; ".join(note + ["пред-IPO/частная компания, не листингованная акция"])
        if cat == "rates":
            return "other", src, "; ".join(note + ["процентные ставки/облигации"])
        return "other", src, "; ".join(note + ["неизвестная категория " + cat])
    # категории нет (основной DEX, снятые билдерские)
    if not dex:                                           # основной DEX: только крипто-токены
        if tick == "PAXG":
            note.append("токен, привязанный к золоту (1 токен = 1 унция): экономически металл")
        if tick.startswith("k") and tick[1:].isupper():
            note.append("k-префикс: единица = 1000 токенов %s, цена и объём за 1000" % tick[1:])
        return "crypto", "hl:main_dex(только крипто; категории в API нет)", "; ".join(note)
    src = "hl:no_category+ticker_map"
    if dex == "hyna" and tick not in METAL:
        if tick.startswith("1000"):
            note.append("множитель 1000: цена за 1000 %s" % tick[4:])
        return "crypto", "hl:dex=hyna(крипто-DEX)", "; ".join(note)
    if tick in METAL:
        return "metal", src, "; ".join(note)
    if tick in ENERGY:
        return "energy", src, "; ".join(note)
    if tick in AGRI:
        return "commodity", src, "; ".join(note)
    if tick == "URANIUM":
        return "commodity", src, "; ".join(note + ["уран: энергетическое сырьё/металл — неоднозначно"])
    if tick in FX:
        return "fx", src, "; ".join(note)
    if tick in RATES:
        return "other", src, "; ".join(note + ["облигации/ставки"])
    if tick in INDEX:
        return "index", src, "; ".join(note)
    if tick in ETF:
        return "etf", src, "; ".join(note)
    if tick in ("BTC", "ETH"):
        return "crypto", src, "; ".join(note)
    if tick in STOCKS:
        return "equity", src, "; ".join(note)
    return "other", "hl:no_category", "; ".join(note + ["API не отдал категорию, тикер не опознан"])


# --- HTTP -----------------------------------------------------------------------------------------------------------------------
class HLHttp(X.Http):
    """POST /info. Общий (на все потоки) ограничитель, общая пауза и адаптивное замедление при 429, 500 'null' = неверный запрос."""
    def __init__(self, rate=1.0, **kw):
        super().__init__(rate=rate, **kw)
        self.base_dt = self.rl.dt
        self.pause_until = 0.0
        self.consec429 = 0
        self.ok_streak = 0
        self.stats.update({"n429": 0, "pause_s": 0.0})

    def post(self, body):
        data = json.dumps(body).encode()
        h = {"User-Agent": self.ua, "Content-Type": "application/json", "Accept": "application/json"}
        last = None
        n_err = 0
        while True:
            d = self.pause_until - time.monotonic()
            if d > 0:
                time.sleep(d)
            self.rl.wait()
            try:
                req = urllib.request.Request(API, data=data, headers=h, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = r.read()
                with self.lock:
                    self.stats["req"] += 1
                    self.consec429 = 0
                    self.ok_streak += 1
                    if self.ok_streak >= 40 and self.rl.dt > self.base_dt:      # серия успехов — вернуть скорость
                        self.rl.dt = max(self.base_dt, self.rl.dt * 0.9)
                        self.ok_streak = 0
                return json.loads(raw)
            except urllib.error.HTTPError as e:
                last = e
                code = e.code
                with self.lock:
                    self.stats["http_err"][code] = self.stats["http_err"].get(code, 0) + 1
                if code == 429:
                    with self.lock:
                        self.consec429 += 1
                        self.ok_streak = 0
                        self.stats["n429"] += 1
                        pause = min(4.0 * self.consec429, 30.0) + random.random()
                        self.stats["pause_s"] += pause
                        self.pause_until = max(self.pause_until, time.monotonic() + pause)
                        self.rl.dt = min(self.rl.dt * 1.25, 5.0)                   # замедлить
                    continue                                                        # 429 не расходует попытки
                n_err += 1
                if code in (400, 404) or (code == 500 and n_err >= 3) or n_err >= self.retries:
                    raise RuntimeError("HTTP %d %s: %s" % (code, str(body)[:120], e.read()[:80].decode("utf-8", "replace")))
                time.sleep(min((2 ** n_err) * 0.5 + random.random(), 20))
            except Exception as e:
                last = e
                n_err += 1
                if n_err >= self.retries:
                    raise RuntimeError("запрос не удался (%d попыток): %s | %s" % (n_err, str(body)[:120], last))
                time.sleep(min((2 ** n_err) * 0.5 + random.random(), 20))
            with self.lock:
                self.stats["retry"] += 1


# --- контракты -------------------------------------------------------------------------------------------------------------------
def load_instruments(http, log):
    dexs = http.post({"type": "perpDexs"})
    try:
        metas = http.post({"type": "allPerpMetas"})
    except Exception as e:
        log("allPerpMetas не удалось (%s) — мета по каждому DEX отдельно" % e)
        metas = [http.post({"type": "meta"})] + [http.post({"type": "meta", "dex": d["name"]}) for d in dexs if d]
    try:
        cats = {k: v for k, v in http.post({"type": "perpCategories"})}
    except Exception as e:
        log("perpCategories не удалось (%s) — классы только по эвристике" % e)
        cats = {}
    tokens = {0: "USDC", 235: "USDE", 268: "USDT0", 360: "USDH"}
    try:
        for t in http.post({"type": "spotMeta"})["tokens"]:
            tokens[t["index"]] = t["name"]
    except Exception as e:
        log("spotMeta не удалось (%s) — имена залога из встроенной таблицы" % e)
    out = []
    for m in metas:
        coll = tokens.get(m.get("collateralToken"), "token%s" % m.get("collateralToken"))
        for u in m["universe"]:
            name = u["name"]
            dex, _, tick = name.rpartition(":")
            cls, src, note = classify(name, cats.get(name))
            notes = ["dex=%s" % (dex or "main"), "maxLev=%s" % u.get("maxLeverage"), "szDec=%s" % u.get("szDecimals")]
            if u.get("onlyIsolated"):
                notes.append("onlyIsolated")
            if u.get("marginMode"):
                notes.append("marginMode=" + u["marginMode"])
            if note:
                notes.append(note)
            out.append({"exchange": EX, "symbol": name, "native_id": name, "base": tick, "quote": "USD", "settle": coll,
                        "class": cls, "class_src": src, "status": "delisted" if u.get("isDelisted") else "active", "contract_size": "1",
                        "tick_size": "", "listed_ms": "", "delisted_ms": "", "funding_interval_h": "1", "taker_fee_bps": "", "maker_fee_bps": "",
                        "note": "; ".join(notes)})
    seen = {}
    for i in out:                                       # коллизии имён файлов (в т.ч. без учёта регистра)
        k = X.safe_name(i["symbol"]).lower()
        if k in seen:
            raise RuntimeError("совпадение имён файлов: %s и %s" % (seen[k], i["symbol"]))
        seen[k] = i["symbol"]
    out.sort(key=lambda i: (i["status"] != "active", i["symbol"]))          # живые вперёд
    return out


# --- свечи / фандинг -------------------------------------------------------------------------------------------------------------
def fetch_candles(http, coin, iv, a, b, now_ms, single=None):
    """Свечи [a,b) -> list (t,o,h,l,c,v,None) только закрытых. Окно <= глубины — один запрос (API сам отдаёт последние ≤5000 в диапазоне),
    иначе вперёд кусками по CHUNK свечей (single=True принудительно один запрос)."""
    step = STEP[iv]
    rows = []
    if b <= a:
        return rows
    t = a
    if single is None:
        single = (b - a) <= (DEPTH + 2) * step
    while t < b:
        t2 = b if single else min(b, t + CHUNK * step)
        j = http.post({"type": "candleSnapshot", "req": {"coin": coin, "interval": iv, "startTime": t, "endTime": t2 - 1}})
        for c in j:
            ts = int(c["t"])
            if ts + step > now_ms:                      # незакрытая свеча
                continue
            rows.append((ts, c["o"], c["h"], c["l"], c["c"], c["v"], None))
        t = t2
    return rows


def fetch_funding(http, coin, a, b):
    rows = []
    t = a
    while t < b:
        j = http.post({"type": "fundingHistory", "coin": coin, "startTime": t, "endTime": b - 1})
        if not j:
            break
        for x in j:
            rows.append((int(x["time"]), x["fundingRate"], 1))
        if len(j) < 500:
            break
        t = int(j[-1]["time"]) + 1
    return rows


def bad_ohlc(rows):
    n = 0
    for r in rows:
        o, h, l, c = float(r[1]), float(r[2]), float(r[3]), float(r[4])
        if not (o > 0 and h > 0 and l > 0 and c > 0) or h < max(o, c) - 1e-12 or l > min(o, c) + 1e-12:
            n += 1
    return n


def done_ok(out, sym, tag):
    p = os.path.join(out, EX, "_state", sym + ".done")
    try:
        with open(p, encoding="utf-8") as f:
            return ("ivs=" + tag) in f.read().split()
    except OSError:
        return False


def main():
    ap = X.common_args("Hyperliquid: свечи (15m в candles15/, 1m в candles/) и фандинг всех перпетуалов", default_rate=1.0, default_workers=2)
    ap.add_argument("--intervals", default="15m,1m", help="какие свечи качать: 15m (весь период, candles15/), 1m (≈5000 последних минут, candles/)")
    ap.add_argument("--no-funding", action="store_true", help="не качать фандинг")
    ap.add_argument("--active-only", action="store_true", help="не опрашивать снятые (isDelisted) контракты")
    args = ap.parse_args()
    start, end = X.parse_ts(args.start), X.parse_ts(args.end)
    ivs = [s.strip() for s in args.intervals.split(",") if s.strip()]
    for s in ivs:
        if s not in STEP:
            ap.error("интервал %r не поддерживается (15m, 1m)" % s)
    ivs.sort(key=lambda s: -STEP[s])                    # сначала грубый (15m): по нему определяется «снят в окне»
    tag = ",".join(ivs) + ("" if not args.no_funding else ",nofund")
    X.ensure_dirs(args.out, EX, intervals=[DIRNAME[s] for s in ivs])
    log = X.make_logger(args.out, EX, "hl")
    http = HLHttp(rate=args.rate)
    t_run = time.time()
    now_ms = int(time.time() * 1000)
    now_min = now_ms // 60000 * 60000
    log("окно %s .. %s UTC, интервалы %s, rate=%.2f/с, workers=%d" % (X.ms_to_str(start), X.ms_to_str(end), ivs, args.rate, args.workers))

    insts = load_instruments(http, log)
    X.write_instruments(args.out, EX, insts)
    cnt = {}
    for i in insts:
        k = (i["class"], i["status"])
        cnt[k] = cnt.get(k, 0) + 1
    log("контрактов %d (живых %d, снятых %d); по классам: %s" % (
        len(insts), sum(1 for i in insts if i["status"] == "active"), sum(1 for i in insts if i["status"] != "active"),
        ", ".join("%s %d (живых %d)" % (c, sum(v for (cc, _), v in cnt.items() if cc == c), cnt.get((c, "active"), 0))
                  for c in sorted({c for c, _ in cnt}))))
    for s in ivs:
        avail = now_min // STEP[s] * STEP[s] - (DEPTH - 1) * STEP[s]
        if avail > start:
            log("ВНИМАНИЕ: интервал %s доступен только с %s UTC (глубина API ≈ %d свечей) — окно неполное" % (s, X.ms_to_str(avail), DEPTH))
    if args.list_only:
        log("--list-only: instruments.csv записан (%d), запросов %d" % (len(insts), http.stats["req"]))
        return 0

    items = insts
    if args.active_only:
        items = [i for i in items if i["status"] == "active"]
    if args.symbols:
        want = [s.strip() for s in args.symbols.split(",") if s.strip()]
        have = {i["symbol"] for i in items}
        for w in want:
            if w not in have:
                log("ВНИМАНИЕ: символа %r нет в списке контрактов" % w)
        items = [i for i in items if i["symbol"] in set(want)]
    if args.limit_symbols:
        items = items[:args.limit_symbols]

    def do(inst):
        sym = X.safe_name(inst["symbol"])
        if not args.force and done_ok(args.out, sym, tag):
            return "пропуск (.done)"
        coin = inst["native_id"]
        delisted = inst["status"] == "delisted"
        info = ["ivs=" + tag]
        last15_end = None
        for k, iv in enumerate(ivs):                    # ivs отсортирован: сначала 15m
            step = STEP[iv]
            if iv == "1m" and delisted and last15_end is not None and last15_end < end - 2 * STEP["15m"]:
                a, b = max(start, last15_end - (DEPTH + 16) * step), last15_end   # снят в окне: «последние 5000» — от его последней свечи
            elif iv == "1m":
                a, b = max(start, now_min - DEPTH * step), min(end, now_min)      # живой: последние ≈5000 минут от «сейчас»
            else:
                a, b = start, min(end, now_min)
            rows = [r for r in fetch_candles(http, coin, iv, a, b, now_ms, single=True if iv == "1m" else None) if start <= r[0] < end]
            if not rows:
                info.append("n%s=0" % iv)
                if k == 0:                                # первый (самый грубый) интервал пуст — данных в окне нет
                    X.mark_done(args.out, EX, sym, "no_data " + " ".join(info))
                    return "нет свечей в окне"
                continue
            n, gaps = X.write_candles(os.path.join(args.out, EX, DIRNAME[iv], sym + ".csv.gz"), rows, step_ms=step, start_ms=start, end_ms=end)
            ts = [r[0] for r in rows]
            info.append("n%s=%d gaps%s=%d first%s=%d last%s=%d bad%s=%d" % (iv, n, iv, gaps, iv, min(ts), iv, max(ts), iv, bad_ohlc(rows)))
            if iv == "15m":
                last15_end = max(ts) + step
        nf = 0
        if not args.no_funding:
            f = fetch_funding(http, coin, start, end)
            nf = X.write_funding(os.path.join(args.out, EX, "funding", sym + ".csv.gz"), f)
            info.append("nf=%d" % nf)
        X.mark_done(args.out, EX, sym, " ".join(info))
        return " ".join(info)

    log("к обработке %d контрактов" % len(items))
    res = X.run_parallel(items, do, args.workers, log)
    log("запросов %d, повторов %d, ошибки HTTP %s, 429: %d (пауз %.0f с), итоговый интервал %.2f с, всего %.0f с" % (
        http.stats["req"], http.stats["retry"], http.stats["http_err"], http.stats["n429"], http.stats["pause_s"], http.rl.dt, time.time() - t_run))
    if res["fails"]:
        log("СБОИ: " + "; ".join("%s: %s" % f for f in res["fails"][:20]))
    return 1 if res["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
