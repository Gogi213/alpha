#!/usr/bin/env python3
"""Bybit (linear, REST v5): минутные свечи + история фандинга ВСЕХ перпетуалов (крипта, акции, ETF, металлы, нефть, валюта…)
за окно [--start, --end) UTC. Только публичные ручки, ключей нет.

  GET /v5/market/instruments-info?category=linear&status=Trading|Closed|PreLaunch|Delivering  (limit 1000, cursor)
  GET /v5/market/kline?category=linear&interval=1&start&end&limit=1000        — ответ от новых к старым; end ВКЛЮЧИТЕЛЬНО
  GET /v5/market/funding/history?category=linear&startTime&endTime&limit=200  — ответ от новых к старым

Результат — формат xdl_common: <out>/bybit/{candles,funding}/<SYMBOL>.csv.gz, <out>/bybit/instruments.csv, метки _state/<SYMBOL>.done.
Особенности (проверены пробой с хоста София 05.11.2026, см. отчёт):
  * без status instruments-info отдаёт только Trading; снятые (Closed) и PreLaunch надо просить отдельно. В Closed лежат ВСЕ снятые
    с 2022 г. — в окно берём только те, у кого deliveryTime >= start (снятые в окне), остальные отбрасываются.
  * kline у снятого контракта: если end > deliveryTime, ответ ломается (пусто или обрезок) — конец окна для Closed режем до
    минуты deliveryTime (включительно). Для живых — до последней ЗАКРЫТОЙ минуты (текущая незакрытая не пишется).
  * volume у linear — в базовой монете контракта (qtyStep 0,001 BTC и т.п.; у 1000PEPE — в «тысячах PEPE»), turnover — в котируемой
    (USDT/USDC); пересчёт не нужен, contract_size=1. Множитель 1000/10000/1000000 вынесен в note.
  * dated-фьючерсы (contractType LinearFutures, BTCUSDT-06NOV26) по умолчанию НЕ берутся (только перпетуалы); --include-dated включает.
  * interval_h фандинга — разрыв до предыдущей выплаты (REST интервал не отдаёт; интервал на Bybit меняется 8→4→2→1 ч сам);
    для первой выплаты окна предыдущая берётся из запроса, расширенного на 9 ч назад.
usage: bybit.py --out DIR [--start 2026-10-01] [--end 2026-11-05] [--rate 10] [--workers 4] [--symbols BTCUSDT,ETHUSDT]
                [--limit-symbols N] [--list-only] [--force] [--include-dated]"""
import os, sys, re, time, threading, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xdl_common import (Http, INSTR_FIELDS, MIN_MS, common_args, ensure_dirs, is_done, make_logger, mark_done, ms_to_str, parse_ts,
                        run_parallel, safe_name, write_candles, write_funding, write_instruments)

EX = "bybit"
BASE = "https://api.bybit.com"
STATUSES = ("Trading", "Closed", "PreLaunch", "Delivering")      # без status API отдаёт только Trading; 'PendingOpen' приходит внутри Closed
TRANSIENT = {10000, 10006, 10016, 10018}                           # таймаут сервиса / «Too many visits» / сбой сервиса / лимит IP
STABLE = {"USDC", "USDT", "USDE", "DAI", "FDUSD", "TUSD", "USD1", "PYUSD", "BUSD"}
GOLD_TOKENS = {"PAXG", "XAUT"}                                     # токены на золото: как в tk488_tradfi_list.py — крипто-активы, оставлены во вселенной
METAL_RX = re.compile(r"\b(gold|silver|platinum|palladium|copper|xau|xag|xpt|xpd)\b", re.I)
ENERGY_RX = re.compile(r"\b(crude|oil|brent|wti|natural gas|natgas|gasoline|heating oil)\b", re.I)
METAL_TICKERS = {"XAU", "XAG", "XPT", "XPD", "HG"}
ENERGY_TICKERS = {"CL", "BZ", "NG", "HO", "RB", "WTI", "BRENT"}
MULT_PREFIX = re.compile(r"^(10+)([A-Z][A-Z0-9]*)$")                # 1000PEPE, 1000000MOG, 10000SATS (1INCH, 1CAT — не множитель)
MULT_SUFFIX = re.compile(r"^([A-Z]{2,}?)(1000|10000|100000|1000000)$")  # SHIB1000


class BybitError(RuntimeError):
    pass


class Api:
    """Обёртка над Http: retCode-ошибки (Bybit отдаёт HTTP 200 + retCode), общая пауза на все потоки при лимитах."""
    def __init__(self, http, log):
        self.http, self.log = http, log
        self.pause_until = 0.0
        self.lock = threading.Lock()
        self.ret = collections.Counter()          # счётчик retCode != 0 и HTTP-ошибок верхнего уровня

    def _pause(self, sec):
        with self.lock:
            self.pause_until = max(self.pause_until, time.monotonic() + sec)

    def call(self, path, params):
        last, nblock, nrate, nsvc = None, 0, 0, 0
        for _ in range(10):
            w = self.pause_until - time.monotonic()
            if w > 0:
                time.sleep(min(w, 120))
            try:
                d = self.http.get_json(BASE + path, params)
            except Exception as e:                       # Http уже повторил сам; тут ловим 403/418/429 (блок/лимит по IP) и 400/404
                last = e
                msg = str(e)
                code = next((c for c in (403, 418, 429) if ("HTTP Error %d" % c) in msg), None)
                if code is None:
                    raise
                nblock += 1
                sec = min(30 * nblock, 120)                # Bybit при превышении IP-лимита режет IP на 10 мин: терпим до ~10 мин суммарно
                with self.lock:
                    self.ret["http_%d" % code] += 1
                self._pause(sec)
                self.log("HTTP %d (лимит/блок IP) %s, общая пауза %d с" % (code, params.get("symbol", ""), sec))
                continue
            rc = d.get("retCode")
            if rc == 0:
                return d.get("result") or {}
            with self.lock:
                self.ret["ret_%s" % rc] += 1
            if rc in TRANSIENT:
                last = BybitError("retCode %s %s" % (rc, d.get("retMsg")))
                if rc in (10006, 10018):                   # квота запросов: пауза на ВСЕ потоки
                    nrate += 1
                    self._pause(min(5 * nrate, 60))
                else:                                      # 10000/10016: сбой сервиса Bybit — повтор того же запроса
                    nsvc += 1
                    self._pause(min(2 * nsvc, 20))
                continue
            raise BybitError("retCode %s %s | %s %s" % (rc, d.get("retMsg"), path, params.get("symbol", "")))
        raise BybitError("не удалось после 10 попыток: %s %s | %s" % (path, params.get("symbol", ""), last))


# ---------------------------------------------------------------------------------------------------------------- список контрактов
def fetch_instruments(api, log):
    allr = {}
    for st in STATUSES:
        cur, n = "", 0
        while True:
            p = {"category": "linear", "limit": 1000, "status": st}
            if cur:
                p["cursor"] = cur
            res = api.call("/v5/market/instruments-info", p)
            lst = res.get("list") or []
            for x in lst:
                allr[x["symbol"]] = x
            n += len(lst)
            cur = res.get("nextPageCursor") or ""
            if not cur or not lst:
                break
        log("instruments-info status=%s: %d" % (st, n))
    return list(allr.values())


def classify(x):
    """-> (class, class_src, note). Не угадываем молча: неизвестный symbolType -> other с пояснением."""
    t = (x.get("symbolType") or "").strip()
    base, name = x.get("baseCoin", ""), x.get("fullName") or ""
    tl = t.lower()
    if tl == "stock":
        return "equity", "symbolType=stock", ""
    if tl == "etf":
        return "etf", "symbolType=ETF", ""
    if tl == "index":
        return "index", "symbolType=index", ""
    if tl == "forex":
        if base in STABLE:
            return "crypto", "symbolType=forex+base=stablecoin", "помечен forex, но базовый актив — стейбл-коин"
        return "fx", "symbolType=forex", ""
    if tl == "commodity":
        if METAL_RX.search(name) or base.upper() in METAL_TICKERS:
            return "metal", "symbolType=commodity+fullName/ticker", ""
        if ENERGY_RX.search(name) or base.upper() in ENERGY_TICKERS:
            return "energy", "symbolType=commodity+fullName/ticker", ""
        return "commodity", "symbolType=commodity", "подкласс (металл/энергия) не определён по имени"
    if tl in ("", "innovation"):
        if base in GOLD_TOKENS:
            return "metal", "ticker-heuristic(gold token)", "токен на золото (PAXG/XAUT): крипто-актив, цена привязана к золоту; в метаданных биржи symbolType пуст"
        return "crypto", ("symbolType=innovation" if tl else "default(symbolType empty)"), ""
    return "other", "symbolType=%s (неизвестное значение)" % t, "неизвестный symbolType — класс не определён"


def multiplier_note(base):
    m = MULT_PREFIX.match(base)
    if m:
        return "multiplier=%s underlying=%s" % (m.group(1), m.group(2))
    m = MULT_SUFFIX.match(base)
    if m:
        return "multiplier=%s underlying=%s" % (m.group(2), m.group(1))
    return ""


def to_row(x):
    cls, src, cnote = classify(x)
    notes = [cnote] if cnote else []
    mn = multiplier_note(x.get("baseCoin", ""))
    if mn:
        notes.append(mn)
    if x.get("symbolType") in ("stock", "ETF"):
        notes.append("region=%s ticker=%s name=%s" % (x.get("marketRegion") or "", x.get("underlyingTicker") or "", x.get("fullName") or ""))
    if x.get("isPreListing"):
        notes.append("pre-listing (isPreListing=true; пред-рынок до листинга базового актива)")
    if "ST" in (x.get("tags") or []):
        notes.append("tag=ST (seed)")
    if x["contractType"] != "LinearPerpetual":
        notes.append("contractType=%s (dated future)" % x["contractType"])
    dl = int(x.get("deliveryTime") or 0)
    fi = int(x.get("fundingInterval") or 0)
    return {"exchange": EX, "symbol": safe_name(x["symbol"]), "native_id": x["symbol"], "base": x.get("baseCoin", ""),
            "quote": x.get("quoteCoin", ""), "settle": x.get("settleCoin", ""), "class": cls, "class_src": src,
            "status": x.get("status", ""), "contract_size": "1",
            "tick_size": (x.get("priceFilter") or {}).get("tickSize", ""), "listed_ms": x.get("launchTime", ""),
            "delisted_ms": dl if (dl and x.get("status") != "Trading" and x["contractType"] == "LinearPerpetual") else "",
            "funding_interval_h": (fi / 60.0) if fi else "", "taker_fee_bps": "", "maker_fee_bps": "",
            "note": "; ".join(notes), "_raw": x}


def in_window(x, start, end, include_dated):
    if x["contractType"] != "LinearPerpetual" and not include_dated:
        return False
    launch = int(x.get("launchTime") or 0)
    if launch >= end:
        return False                                     # листинг после окна
    if x.get("status") in ("Closed", "PendingOpen", "Delivering"):
        dl = int(x.get("deliveryTime") or 0)
        return dl == 0 or dl >= start                    # снят до начала окна — данных в окне нет
    return True


# ---------------------------------------------------------------------------------------------------------------- свечи
def fetch_candles(api, x, start_ms, end_ms):
    """Свечи [start_ms, end_ms) (end_ms уже обрезан по закрытой минуте и по deliveryTime). Возврат: (rows, число запросов)."""
    sym = x["symbol"]
    launch_floor = (int(x.get("launchTime") or 0) // MIN_MS) * MIN_MS
    rows, cur_end, nreq = {}, end_ms - 1, 0               # end у Bybit включительно
    while cur_end >= start_ms:
        res = api.call("/v5/market/kline", {"category": "linear", "symbol": sym, "interval": "1", "start": start_ms,
                                            "end": cur_end, "limit": 1000})
        nreq += 1
        lst = res.get("list") or []
        if not lst:
            break
        oldest = min(int(k[0]) for k in lst)
        for k in lst:
            t = int(k[0])
            if start_ms <= t < end_ms:
                rows[t] = (t, k[1], k[2], k[3], k[4], k[5], k[6])
        # стоп: дошли до начала окна или до листинга; «страница короче лимита» сигналом конца НЕ считаем (у снятых ответ бывает обрезком)
        if oldest <= start_ms or oldest <= launch_floor or oldest > cur_end:
            break
        cur_end = oldest - 1
    return list(rows.values()), nreq


def bad_ohlc(rows):
    n = 0
    for r in rows:
        try:
            o, h, l, c = float(r[1]), float(r[2]), float(r[3]), float(r[4])
        except ValueError:
            n += 1
            continue
        if not (o > 0 and h > 0 and l > 0 and c > 0) or h < max(o, c) or l > min(o, c):
            n += 1
    return n


# ---------------------------------------------------------------------------------------------------------------- фандинг
def fetch_funding(api, x, start_ms, end_ms):
    """Выплаты [start_ms, end_ms) + интервал. Возврат: (rows[(time_ms, rate, interval_h)], число запросов, число подстановок interval)."""
    sym = x["symbol"]
    fi_h = (int(x.get("fundingInterval") or 0) / 60.0) or None
    if x["contractType"] != "LinearPerpetual" or not fi_h:
        return [], 0, 0                                    # dated-фьючерсы фандинга не имеют
    lo = start_ms - 9 * 3600_000                           # предыдущая выплата нужна для интервала первой записи окна
    got, cur_end, nreq = {}, end_ms - 1, 0
    while cur_end >= lo:
        res = api.call("/v5/market/funding/history", {"category": "linear", "symbol": sym, "startTime": lo, "endTime": cur_end, "limit": 200})
        nreq += 1
        lst = res.get("list") or []
        if not lst:
            break
        for k in lst:
            got[int(k["fundingRateTimestamp"])] = k["fundingRate"]
        oldest = min(int(k["fundingRateTimestamp"]) for k in lst)
        if len(lst) < 200 or oldest <= lo or oldest > cur_end:
            break
        cur_end = oldest - 1
    ts = sorted(got)
    out, fallback = [], 0
    for i, t in enumerate(ts):
        if t < start_ms or t >= end_ms:
            continue
        if i > 0:
            iv = (t - ts[i - 1]) / 3.6e6
        elif len(ts) > 1:
            iv = (ts[1] - t) / 3.6e6                       # нет предыдущей (листинг в окне): по следующей
        else:
            iv = None
        if iv is None or iv < 0.9 or iv > 8.01:            # дыра/аномалия: берём текущий интервал из instruments-info
            iv, fallback = fi_h, fallback + 1
        out.append((t, got[t], round(iv, 4)))
    return out, nreq, fallback


# ---------------------------------------------------------------------------------------------------------------- один контракт
def one(item, a, start, end, api):
    x, sym = item["_raw"], item["symbol"]
    now_closed = (int(time.time() * 1000) // MIN_MS) * MIN_MS      # последняя закрытая минута = < этого значения
    eff_end = min(end, now_closed)
    dl = int(x.get("deliveryTime") or 0)
    if x.get("status") in ("Closed", "PendingOpen", "Delivering") and dl:
        eff_end = min(eff_end, (dl // MIN_MS) * MIN_MS + MIN_MS)   # без этого ответ снятого контракта ломается
    if eff_end <= start:
        write_candles(os.path.join(a.out, EX, "candles", sym + ".csv.gz"), [], start_ms=start, end_ms=end)
        write_funding(os.path.join(a.out, EX, "funding", sym + ".csv.gz"), [])
        mark_done(a.out, EX, sym, "empty window")
        return "пустое окно"
    crows, nc = fetch_candles(api, x, start, eff_end)
    ncand, gaps = write_candles(os.path.join(a.out, EX, "candles", sym + ".csv.gz"), crows, start_ms=start, end_ms=eff_end)
    frows, nf, fb = fetch_funding(api, x, start, end)
    nfund = write_funding(os.path.join(a.out, EX, "funding", sym + ".csv.gz"), frows)
    bad = bad_ohlc(crows)
    info = "candles=%d gaps=%d bad_ohlc=%d funding=%d iv_fallback=%d req_candles=%d req_funding=%d eff_end=%s" % (
        ncand, gaps, bad, nfund, fb, nc, nf, ms_to_str(eff_end))
    mark_done(a.out, EX, sym, info)
    return info


def main():
    ap = common_args("Bybit linear: минутные свечи + фандинг всех перпетуалов за окно", default_rate=10.0, default_workers=4)
    ap.add_argument("--include-dated", action="store_true", help="включить dated-фьючерсы (LinearFutures); по умолчанию только перпетуалы")
    a = ap.parse_args()
    start, end = parse_ts(a.start), parse_ts(a.end)
    if end <= start:
        sys.exit("end <= start")
    ensure_dirs(a.out, EX)
    log = make_logger(a.out, EX, "list" if a.list_only else "run")
    http = Http(rate=a.rate, retries=4)
    api = Api(http, log)
    log("окно [%s, %s) UTC, rate=%.1f/с, workers=%d" % (ms_to_str(start), ms_to_str(end), a.rate, a.workers))

    raw = fetch_instruments(api, log)
    sel = [x for x in raw if in_window(x, start, end, a.include_dated)]
    skipped = collections.Counter()
    in_sel = {x["symbol"] for x in sel}
    for x in raw:
        if x["symbol"] not in in_sel:
            skipped["dated" if x["contractType"] != "LinearPerpetual" and not a.include_dated else
                    "launch>=end" if int(x.get("launchTime") or 0) >= end else "delisted<start"] += 1
    rows = sorted((to_row(x) for x in sel), key=lambda r: r["symbol"])
    write_instruments(a.out, EX, rows)
    cnt = collections.Counter((r["class"], r["status"]) for r in rows)
    log("instruments.csv: %d контрактов в окне (всего в API %d, отброшено %s)" % (len(rows), len(raw), dict(skipped)))
    for k, v in sorted(cnt.items()):
        log("  класс %-9s статус %-10s %d" % (k[0], k[1], v))
    if a.list_only:
        return

    items = rows
    if a.symbols:
        want = {s.strip() for s in a.symbols.split(",") if s.strip()}
        miss = want - {r["native_id"] for r in rows} - {r["symbol"] for r in rows}
        if miss:
            log("ВНИМАНИЕ: не найдены в окне: %s" % ",".join(sorted(miss)))
        items = [r for r in rows if r["native_id"] in want or r["symbol"] in want]
    if a.limit_symbols:
        items = items[:a.limit_symbols]
    if not a.force:
        n0 = len(items)
        items = [r for r in items if not is_done(a.out, EX, r["symbol"])]
        log("пропущено готовых: %d" % (n0 - len(items)))
    log("к закачке: %d контрактов" % len(items))
    res = run_parallel(items, lambda it: one(it, a, start, end, api), a.workers, log)
    log("запросов успешных %d, повторов %d, http-ошибок %s, retCode/верхнеуровневые %s" % (
        http.stats["req"], http.stats["retry"], dict(http.stats["http_err"]), dict(api.ret)))
    for s, e in res["fails"]:
        log("СБОЙ %s: %s" % (s, e))
    sys.exit(1 if res["fail"] else 0)


if __name__ == "__main__":
    main()
