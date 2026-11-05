#!/usr/bin/env python3
"""OKX: свечи 1 м и история фандинга ВСЕХ SWAP-контрактов (крипта, акции, ETF, индексы, металлы, энергия) за окно. Только публичные данные.

Источники (замерено 05.11.2026 с VPS София):
  * список контрактов  GET /api/v5/public/instruments?instType=SWAP  (отдаёт только live; снятые за окно — через --symbols);
  * свечи, режим bulk  GET /api/v5/public/market-data-history?module=2 (список файлов) + файлы static.okx.com/cdn/okex/traderecords/
        candlesticks/{monthly/YYYYMM|daily/YYYYMMDD}/<INST>-candlesticks-<дата>.zip — публичная пакетная выгрузка, СУТКИ И МЕСЯЦЫ ПО UTC+8,
        самый свежий daily появляется с лагом (на 05.11 17:30 UTC последний — 2026-11-04 UTC+8 = до 04.11 16:00 UTC);
  * свечи, режим rest   GET /api/v5/market/history-candles (bar=1m, limit 100, новые→старые, after = «старше ts», before = «новее ts»);
  * фандинг             GET /api/v5/public/funding-rate-history (limit 100, новые→старые, after = «старше fundingTime»).
--source auto (по умолчанию): bulk там, где файлы есть, хвост/дыры дотягивает REST; rest — только REST (504 запроса/контракт на 35 суток);
bulk — только файлы (без REST-хвоста). Результат (формат xdl_common): <out>/okx/{candles,funding,instruments.csv,_state,_logs}.

Единицы: v = volCcy (объём в БАЗОВОЙ монете/акции/унциях; у линейных своп-контрактов == vol * ctVal * ctMult, проверено; у inverse-контрактов
BTC-USD-SWAP и т. п. volCcy — монеты), quote_v = volCcyQuote (USDT у линейных, USD у inverse). Фандинг: rate = realizedRate (фактически
начисленная), если пусто — fundingRate; interval_h считается по разности соседних fundingTime (первая запись окна — по предыдущей записи).
Свечи с confirm != 1 (незакрытые) не пишутся. Дыры (минуты без сделок — OKX свечу не отдаёт) не дорисовываются.
"""
import os, sys, io, csv, json, time, zipfile, datetime, urllib.request, urllib.error
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xdl_common as X

EX = "okx"
API = "https://www.okx.com"
CDN = "https://static.okx.com/cdn/okex/traderecords/candlesticks"
UTC8 = 8 * 3600 * 1000
DAY = 86400000
MONTHLY_MIN_DAYS = 6        # месячный файл (~1 МБ у ликвидных), если нужно >= стольких суток месяца; иначе суточные (~0,04 МБ)

# --- классификация (instCategory OKX: 1 крипто, 3 акции, 4 товары, 5 валюта; ETF/индексы/металлы/энергия — по тикеру) ---
INDEX = {"US100", "US500", "JP225", "KR200"}
ETF = {"SPY", "QQQ", "IWM", "SMH", "TLT", "TMF", "TQQQ", "SQQQ", "SOXL", "SOXS", "UVXY", "USO", "URNM", "XBI", "XLE", "EWJ", "EWY",
       "EWZ", "EWT", "KORU", "TSLL", "NVDL", "MSTU", "MUU", "MVLL", "CSOPSKHYNIX2L", "CSOPSAMSUNG2L"}
METAL = {"XAU", "XAG", "XPT", "XPD", "XCU"}
ENERGY = {"BZ", "CL", "NG"}


def classify(x, overrides):
    iid = x["instId"]
    base = iid.split("-")[0]
    cat = x.get("instCategory", "")
    note = []
    if base in overrides:
        return overrides[base], "override", "class из --class-overrides"
    if cat == "1":
        cls, src = "crypto", "okx:instCategory=1"
        if base in ("USDC", "USDT", "DAI"):
            note.append("stablecoin-контракт")
    elif cat == "3":
        src = "okx:instCategory=3"
        if x.get("ruleType") == "pre_market":
            cls = "other"
            note.append("ruleType=pre_market: пред-IPO/приватная компания, не листингованная акция")
        elif base in INDEX:
            cls, src = "index", src + "+ticker_map"
        elif base in ETF:
            cls, src = "etf", src + "+ticker_map"
        else:
            cls = "equity"
            note.append("OKX category=3 (акции); флага ETF в API нет — часть тикеров может быть ETF/ETN")
    elif cat == "4":
        src = "okx:instCategory=4+ticker_map"
        if base in METAL:
            cls = "metal"
        elif base in ENERGY:
            cls = "energy"
        else:
            cls = "other"
            note.append("OKX category=4 (товары), но тикер не опознан (H100 — похоже на индекс аренды GPU)")
    elif cat == "5":
        cls, src = "fx", "okx:instCategory=5"
    else:
        cls, src = "other", "okx:instCategory=%s" % cat
        note.append("неизвестный instCategory")
    return cls, src, "; ".join(note)


def build_instruments(data, overrides):
    rows = []
    for x in data:
        iid = x["instId"]
        parts = iid.split("-")
        cls, src, note = classify(x, overrides)
        notes = [note] if note else []
        if x.get("ctType") == "inverse":
            notes.append("inverse (в монете): ctVal=%s %s на контракт, расчёт в %s" % (x.get("ctVal"), x.get("ctValCcy"), x.get("settleCcy")))
        if x.get("ctMult") not in ("", "1", None):
            notes.append("ctMult=%s" % x.get("ctMult"))
        try:
            cs = str(Decimal(x.get("ctVal") or "0") * Decimal(x.get("ctMult") or "1").normalize())
        except Exception:
            cs = x.get("ctVal", "")
        rows.append({"exchange": EX, "symbol": iid, "native_id": iid, "base": parts[0], "quote": parts[1] if len(parts) > 1 else "",
                     "settle": x.get("settleCcy", ""), "class": cls, "class_src": src, "status": x.get("state", ""), "contract_size": cs,
                     "tick_size": x.get("tickSz", ""), "listed_ms": x.get("listTime", ""), "delisted_ms": "", "funding_interval_h": "",
                     "taker_fee_bps": "", "maker_fee_bps": "", "note": "; ".join(notes),
                     "_ctType": x.get("ctType", ""), "_ctVal": x.get("ctVal", ""), "_ctMult": x.get("ctMult", "1")})
    return rows


# --- HTTP ---
class BinHttp(X.Http):
    """Скачивание байтов с CDN: 404 -> None (файла нет), прочее — повторы."""
    def get_bytes(self, url):
        last = None
        for attempt in range(self.retries):
            self.rl.wait()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": self.ua})
                with urllib.request.urlopen(req, timeout=60) as r:
                    raw = r.read()
                with self.lock:
                    self.stats["req"] += 1
                return raw
            except urllib.error.HTTPError as e:
                last = e
                with self.lock:
                    self.stats["http_err"][e.code] = self.stats["http_err"].get(e.code, 0) + 1
                if e.code == 404:
                    return None
                time.sleep(min((2 ** attempt) * 0.5, 30))
            except Exception as e:
                last = e
                time.sleep(min((2 ** attempt) * 0.5, 30))
            with self.lock:
                self.stats["retry"] += 1
        raise RuntimeError("скачивание не удалось: %s | %s" % (url[:160], last))


def api(http, path, params):
    """Вызов REST OKX: code != '0' -> исключение; перегрузка (50011/50013/50026/50061) — повтор с паузой."""
    last = None
    for attempt in range(8):
        j = http.get_json(API + path, params)
        code = j.get("code")
        if code == "0":
            return j.get("data", [])
        last = "%s %s" % (code, j.get("msg"))
        if code in ("50011", "50013", "50026", "50061", "50001"):
            time.sleep(min(2 * (attempt + 1), 20))
            continue
        break
    raise RuntimeError("OKX %s %s: %s" % (path, json.dumps(params)[:100], last))


# --- время UTC+8 (границы суток/месяцев в пакетных файлах) ---
def day8_start(ms):
    return ((ms + UTC8) // DAY) * DAY - UTC8


def d8(ms_day8_start):
    return datetime.datetime.fromtimestamp((ms_day8_start + UTC8) / 1000, datetime.timezone.utc)


def month8_bounds(ms):
    """Начало и конец (исключительно) месяца по UTC+8, в котором лежит ms."""
    dt = datetime.datetime.fromtimestamp((ms + UTC8) / 1000, datetime.timezone.utc)
    a = datetime.datetime(dt.year, dt.month, 1, tzinfo=datetime.timezone.utc)
    b = datetime.datetime(dt.year + (dt.month == 12), dt.month % 12 + 1, 1, tzinfo=datetime.timezone.utc)
    return int(a.timestamp() * 1000) - UTC8, int(b.timestamp() * 1000) - UTC8


def bulk_available_days(http, lo, hi):
    """Множество дат 'YYYY-MM-DD' (UTC+8), для которых есть суточный файл allswap (листинг market-data-history, окна <= 10 суток)."""
    days = set()
    b = day8_start(lo)
    last = day8_start(hi - 1) + DAY
    while b < last:
        e = min(b + 9 * DAY, last)
        try:
            data = api(http, "/api/v5/public/market-data-history",
                       {"module": 2, "instType": "SWAP", "dateAggrType": "daily", "begin": b, "end": e})
        except Exception as ex:
            raise RuntimeError("листинг пакетных файлов: %s" % ex)
        for g in data:
            for det in g.get("details", []):
                for f in det.get("groupDetails", []):
                    nm = f.get("filename", "")
                    if nm.startswith("allswap-candlesticks-"):
                        days.add(nm[len("allswap-candlesticks-"):-len(".zip")])
        b = e
    return days


def parse_zip(raw, lo, hi, ctval_fallback):
    out = []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for name in z.namelist():
            with z.open(name) as f:
                rd = csv.reader(io.TextIOWrapper(f, encoding="utf-8", newline=""))
                hdr = next(rd, None)
                if not hdr:
                    continue
                ix = {h.strip(): i for i, h in enumerate(hdr)}
                it, io_, ih, il, ic = ix["open_time"], ix["open"], ix["high"], ix["low"], ix["close"]
                iv, ivc, ivq, icf = ix.get("vol"), ix.get("vol_ccy"), ix.get("vol_quote"), ix.get("confirm")
                for r in rd:
                    if not r or (icf is not None and r[icf] != "1"):
                        continue
                    t = int(r[it])
                    if t < lo or t >= hi:
                        continue
                    v = r[ivc] if ivc is not None else ""
                    if v == "" and ctval_fallback is not None and iv is not None and r[iv] != "":
                        v = str(Decimal(r[iv]) * ctval_fallback)
                    out.append((t, r[io_], r[ih], r[il], r[ic], v, r[ivq] if ivq is not None else ""))
    return out


def load_file(http, url, lo, hi, ctv):
    """Скачать и разобрать zip; None — файла нет (404). Битый/недокачанный архив — до 3 попыток."""
    last = None
    for _ in range(3):
        raw = http.get_bytes(url)
        if raw is None:
            return None
        try:
            return parse_zip(raw, lo, hi, ctv)
        except (zipfile.BadZipFile, KeyError, ValueError, StopIteration) as e:
            last = e
            time.sleep(1)
    raise RuntimeError("битый архив %s: %s" % (url[:140], last))


def merge_cover(cov):
    cov = sorted(cov)
    res = []
    for a, b in cov:
        if res and a <= res[-1][1]:
            res[-1] = (res[-1][0], max(res[-1][1], b))
        else:
            res.append((a, b))
    return res


def gaps_of(lo, hi, cov):
    out, cur = [], lo
    for a, b in merge_cover(cov):
        if b <= cur:
            continue
        if a > cur:
            out.append((cur, min(a, hi)))
        cur = max(cur, b)
        if cur >= hi:
            break
    if cur < hi:
        out.append((cur, hi))
    return [g for g in out if g[0] < g[1]]


def rest_candles(http, inst, lo, hi):
    """Свечи [lo, hi) постранично: after=курсор (старше), before=lo-1 (новее), 100 за запрос, пока страница не пустая."""
    rows, cursor, pages = [], hi, 0
    while True:
        data = api(http, "/api/v5/market/history-candles", {"instId": inst, "bar": "1m", "after": cursor, "before": lo - 1, "limit": 100})
        pages += 1
        if not data:
            break
        tmin = min(int(r[0]) for r in data)
        for r in data:
            if len(r) > 8 and r[8] != "1":
                continue
            t = int(r[0])
            if lo <= t < hi:
                rows.append((t, r[1], r[2], r[3], r[4], r[6], r[7]))
        if tmin >= cursor or tmin <= lo:
            break
        cursor = tmin
    return rows, pages


def rest_funding(http, inst, lo, hi):
    """Ставки с fundingTime в [lo, hi) + одна предыдущая запись (для interval_h первой). Возвращает [(time_ms, rate, interval_h)]."""
    raw, cursor = {}, hi
    while True:
        data = api(http, "/api/v5/public/funding-rate-history", {"instId": inst, "after": cursor, "limit": 100})
        if not data:
            break
        for d in data:
            rate = d.get("realizedRate") or d.get("fundingRate") or ""
            raw[int(d["fundingTime"])] = rate
        tmin = min(int(d["fundingTime"]) for d in data)
        if tmin < lo or tmin >= cursor:
            break
        cursor = tmin
    ts = sorted(raw)
    out = []
    for i, t in enumerate(ts):
        if i > 0:
            ih = (t - ts[i - 1]) / 3600000.0
        elif len(ts) > 1:
            ih = (ts[1] - t) / 3600000.0
        else:
            ih = None
        if lo <= t < hi:
            out.append((t, raw[t], (int(ih) if ih is not None and ih == int(ih) else (round(ih, 3) if ih is not None else None))))
    return out


def main():
    ap = X.common_args("OKX: свечи 1 м + фандинг всех SWAP за окно", default_rate=8.0, default_workers=6)
    ap.add_argument("--source", choices=("auto", "bulk", "rest"), default="auto", help="auto: пакетные файлы + REST-хвост; rest: только REST; bulk: только файлы")
    ap.add_argument("--bulk-rate", type=float, default=8.0, help="скачиваний файлов с CDN в секунду (процесс)")
    ap.add_argument("--class-overrides", default="", help="JSON-файл {тикер: класс} — поправки к классификации")
    ap.add_argument("--no-funding", action="store_true")
    args = ap.parse_args()
    start, end = X.parse_ts(args.start), X.parse_ts(args.end)
    out = os.path.abspath(args.out)
    X.ensure_dirs(out, EX)
    log = X.make_logger(out, EX, "okx")
    api_http = X.Http(rate=args.rate)
    cdn_http = BinHttp(rate=args.bulk_rate)
    overrides = json.load(open(args.class_overrides, encoding="utf-8")) if args.class_overrides else {}

    log("окно %s .. %s UTC; source=%s rate=%.1f bulk-rate=%.1f workers=%d" % (X.ms_to_str(start), X.ms_to_str(end), args.source, args.rate, args.bulk_rate, args.workers))
    insts = build_instruments(api(api_http, "/api/v5/public/instruments", {"instType": "SWAP"}), overrides)
    by_sym = {i["symbol"]: i for i in insts}
    cnt = {}
    for i in insts:
        cnt[i["class"]] = cnt.get(i["class"], 0) + 1
    log("контрактов SWAP: %d; по классам: %s; статусы: %s" % (len(insts), json.dumps(cnt, ensure_ascii=False),
        json.dumps({s: sum(1 for i in insts if i["status"] == s) for s in set(i["status"] for i in insts)})))
    X.write_instruments(out, EX, insts)
    if args.list_only:
        print("instruments.csv ->", os.path.join(out, EX, "instruments.csv"))
        return 0

    todo = insts
    if args.symbols:
        todo = []
        for s in [s.strip() for s in args.symbols.split(",") if s.strip()]:
            if s in by_sym:
                todo.append(by_sym[s])
            else:   # снятый/не из live-списка: пробуем скачать, класс неизвестен
                p = s.split("-")
                row = {"exchange": EX, "symbol": s, "native_id": s, "base": p[0], "quote": p[1] if len(p) > 1 else "", "class": "other",
                       "class_src": "none", "status": "not-in-live-list", "note": "нет в live-списке instruments (снят?)", "_ctType": "", "_ctVal": "", "_ctMult": "1"}
                todo.append(row)
    if args.limit_symbols:
        todo = todo[:args.limit_symbols]

    avail = set()
    if args.source != "rest":
        avail = bulk_available_days(api_http, start, end)
        log("пакетные суточные файлы (allswap, UTC+8): %d дат, последняя %s" % (len(avail), max(avail) if avail else "-"))
    avail_last = max(avail) if avail else ""

    def do_symbol(sym):
        inst = todo_by_sym[sym]
        fn = X.safe_name(sym)
        if not args.force and X.is_done(out, EX, fn):
            return "пропущено (.done)"
        lo = max(start, (int(inst["listed_ms"]) // 60000) * 60000) if inst.get("listed_ms") else start
        hi = end
        rows, cov, nfile, nrest = [], [], 0, 0
        month_tried = set()
        ctv = None
        if inst["_ctType"] == "linear":
            try:
                ctv = Decimal(inst["_ctVal"] or "0") * Decimal(inst["_ctMult"] or "1")
            except Exception:
                ctv = None
        if lo < hi and args.source != "rest":
            d = day8_start(lo)
            last_d = day8_start(hi - 1)
            while d <= last_d:
                ds = d8(d).strftime("%Y-%m-%d")
                ma, mb = month8_bounds(d)
                need_days = (min(mb, last_d + DAY) - max(ma, day8_start(lo))) // DAY
                last_day_of_month = d8(mb - DAY).strftime("%Y-%m-%d")
                use_month = need_days >= MONTHLY_MIN_DAYS and avail_last >= last_day_of_month and ma not in month_tried
                got = False
                if use_month:
                    month_tried.add(ma)
                    ym = d8(ma).strftime("%Y%m")
                    got_rows = load_file(cdn_http, "%s/monthly/%s/%s-candlesticks-%s.zip?v=1001" % (CDN, ym, sym, d8(ma).strftime("%Y-%m")), lo, hi, ctv)
                    if got_rows is not None:
                        rows += got_rows
                        cov.append((ma, mb))
                        nfile += 1
                        got = True
                    if got:           # нет месячного файла -> этот месяц суточными
                        d = mb
                        continue
                if ds in avail:
                    got_rows = load_file(cdn_http, "%s/daily/%s/%s-candlesticks-%s.zip?v=1001" % (CDN, d8(d).strftime("%Y%m%d"), sym, ds), lo, hi, ctv)
                    if got_rows is not None:
                        rows += got_rows
                        cov.append((d, d + DAY))
                        nfile += 1
                d += DAY
        npg = 0
        if args.source != "bulk" and lo < hi:
            for a, b in gaps_of(lo, hi, cov):
                r, pg = rest_candles(api_http, sym, a, b)
                rows += r
                npg += pg
                nrest += len(r)
        n, gaps = X.write_candles(os.path.join(out, EX, "candles", fn + ".csv.gz"), rows, start_ms=start, end_ms=end)
        nf, fh = 0, ""
        if not args.no_funding:
            fr = rest_funding(api_http, sym, start, end) if lo < hi else []
            nf = X.write_funding(os.path.join(out, EX, "funding", fn + ".csv.gz"), fr)
            fh = fr[-1][2] if fr and fr[-1][2] is not None else ""
        X.mark_done(out, EX, fn, json.dumps({"c": n, "g": gaps, "f": nf, "fh": fh, "files": nfile, "rest_rows": nrest, "rest_pages": npg}))
        return "свечей %d (дыр %d; файлов %d, REST %d стр./%d стр.) фандинг %d (шаг %sч)" % (n, gaps, nfile, nrest, npg, nf, fh)

    todo_by_sym = {i["symbol"]: i for i in todo}
    res = X.run_parallel([i["symbol"] for i in todo], do_symbol, args.workers, log)
    log("HTTP API: %s | CDN: %s" % (json.dumps(api_http.stats), json.dumps(cdn_http.stats)))
    # funding_interval_h в instruments.csv — из меток .done (по всем символам, не только этого запуска)
    try:
        for i in insts:
            p = os.path.join(out, EX, "_state", X.safe_name(i["symbol"]) + ".done")
            if os.path.exists(p):
                i["funding_interval_h"] = json.load(open(p)).get("fh", "")
        X.write_instruments(out, EX, insts)
    except Exception as e:
        log("instruments.csv: не обновлён funding_interval_h: %s" % e)
    return 1 if res["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
