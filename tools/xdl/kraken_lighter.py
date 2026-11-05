#!/usr/bin/env python3
"""Закачка ПУБЛИЧНЫХ минутных свечей и фандинга: Kraken Futures и Lighter (стат-арбитраж «на всём», 05.11.2026).

  python3 kraken_lighter.py --exchange kraken  --out /data/xdl --rate 3 --workers 2
  python3 kraken_lighter.py --exchange lighter --out /data/xdl --rate 3 --workers 2

Только стандартная библиотека, ключей и авторизации нет. Формат результата — xdl_common (см. его докстринг):
  <out>/<exchange>/candles/<SYMBOL>.csv.gz, funding/<SYMBOL>.csv.gz, instruments.csv, _state/<SYMBOL>.done, _logs/.

KRAKEN FUTURES (https://futures.kraken.com)
  список   : GET /derivatives/api/v3/instruments (219 шт.: 209 перпетуалов PF_* + 10 датированных FF_*; PI_* сейчас нет — код на
             случай их появления есть, но НЕ проверен). По умолчанию качаются только перпетуалы PF_/PI_; датированные
             FF_/FI_ попадают в instruments.csv, а качаются лишь с --include-dated или по явному --symbols.
  свечи    : GET /api/charts/v1/trade/{sym}/1m?from=&to= (секунды, ОБА конца включительно; максимум 2000 свечей в ответе, по
             возрастанию; пагинация — флаг more_candles, следующий from = время последней свечи + 60 с). Поле volume — в
             контрактах; у всех линейных PF_ contractSize = 1, т.е. это базовые единицы (v = volume * contractSize).
             quote_v биржа не отдаёт — пусто. Свечей до первой сделки нет; после первой сделки биржа сама «дорисовывает»
             пустые минуты плоскими свечами (v=0, o=h=l=c=закрытие предыдущей) — по умолчанию такие строки НЕ пишем
             (--keep-filler оставляет).
  фандинг  : GET /derivatives/api/v4/historicalfundingrates?symbol= — вся история (≈1 год, шаг 1 ч) ОДНИМ запросом, фильтров
             по времени нет (~0,3-1 МБ на контракт). В файл пишем relativeFundingRate (доля за 1 час от цены, >0: лонги платят
             шортам), interval_h=1; absolute fundingRate (в USD на контракт) не пишем. Для FF_ фандинга нет.

LIGHTER (https://mainnet.zklighter.elliot.ai)
  список   : GET /api/v1/orderBookDetails (перпы + спот, ВКЛЮЧАЯ 7 неактивных, которых нет в /api/v1/orderBooks) + /api/v1/orderBooks
             (объединяем по market_id). Короткоживущие «предсказательные» *-UPDOWN-*-<unix> в списках бывают (в /exchangeStats
             ≈244 штуки) — помечаются class=other, note=prediction и НЕ качаются никогда. Спот (market_type=spot) попадает в
             instruments.csv, а качается лишь с --include-spot или по явному --symbols.
  свечи    : GET /api/v1/candles?market_id=&resolution=1m&start_timestamp=&end_timestamp=&count_back=0 (путь /candlesticks
             закрыт CDN 403). Время — секунды (мс тоже принимаются). Окно [start, end): start включительно, end исключительно.
             Ответ — НЕПРЕРЫВНЫЙ ряд минут, кончающийся у end: сервер берёт последние ~500 СДЕЛОЧНЫХ свечей окна и между ними
             и до первой из них дорисовывает пустые минуты (v=0, ключи O/H/L/C — признак «дорисовано»); поэтому размер ответа
             500…∞ и пагинация идёт НАЗАД: end = время первой полученной свечи, пока она позже start. v/V — базовый/котируемый
             объём. Дорисованные минуты по умолчанию НЕ пишем (--keep-filler оставляет).
  фандинг  : GET /api/v1/fundings?market_id=&resolution=1h&start_timestamp=&end_timestamp=&count_back=0 (только 1h; максимум
             ≈750 строк, берутся последние — окно режем по 700 ч; строка t отдаётся при start+3600 <= t < end, т.е. start у
             биржи «сдвинут» на час). timestamp — секунды, начало часа. Поле rate — в ПРОЦЕНТАХ
             за час, округлено до 4 знаков (0,0012 = 0,0012 %), знак — в поле direction ('long' — платят лонги, т.е. >0;
             'short' — платят шорты, <0). В файл пишем знаковую ДОЛЮ за час: ±rate/100, interval_h=1. Точнее (8 знаков) поле
             value (USD на единицу базы) — не пишем.

Лимиты (замеры с VPS София, один IP): Kraken — 211 запросов подряд при 3/с без 429; Lighter — 2/с держит (150-270 запросов подряд),
3/с после ~100 запросов даёт 429 {"code":23000}, а при затяжном 429 появились и 405 от CloudFront; поэтому по умолчанию Lighter 2/с.
Все метки времени — мс UTC. Окно [start, end); незакрытая текущая минута не пишется.
"""
import os, sys, re, json, time, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xdl_common import (Http, common_args, parse_ts, ensure_dirs, make_logger, write_candles, write_funding,
                        write_instruments, is_done, mark_done, safe_name, run_parallel, MIN_MS)

KR = "https://futures.kraken.com"
LT = "https://mainnet.zklighter.elliot.ai"
UTC = datetime.timezone.utc


# ───────────────────────────── общие мелочи ─────────────────────────────
def iso_ms(s):
    """'2026-10-23T08:00:30Z' / '...30.307Z' / '...03.286012476Z' -> мс UTC."""
    m = re.match(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?(Z|\+00:00)?$", str(s).strip())
    if not m:
        raise ValueError("не разобрано время: %r" % (s,))
    dt = datetime.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
    ms = int(dt.timestamp()) * 1000
    if m.group(2):
        ms += int(round(float(m.group(2)) * 1000))
    return ms


def num(x, nd=10):
    """Число -> короткая строка без хвостов float (8.76029999999999 -> 8.7603)."""
    return repr(round(float(x), nd))


def now_ms():
    return int(time.time() * 1000)


def add_note(*parts):
    return "; ".join(p for p in parts if p)


# ───────────────────────────── KRAKEN: классификация ─────────────────────────────
KR_CRYPTO_CATS = {"DeFi", "Layer 1", "Layer 2", "Meme", "Community", "Gaming", "AI", "Web3", "Privacy", "Utility", "DePIN",
                  "DEX", "Infrastructure", "Real-world assets"}
KR_METAL = {"XAU", "XAG", "XCU"}                       # золото, серебро, медь (категория Commodities)
KR_ENERGY = {"WTIOIL", "BRENTOIL", "NATGAS"}
KR_GOLD_TOKENS = {"PAXG", "XAUT"}                      # токены, обеспеченные золотом (у биржи лежат в категории Stablecoin)
KR_STABLES = {"USDT", "USDC"}
KR_XSTOCK_ETF = {"GLDx", "QQQx", "SPYx"}               # токенизированные ETF среди xStocks (таблица по тикеру, не метаданные биржи)


def kraken_class(i):
    """-> (class, class_src, note). Неоднозначное — other + пояснение."""
    cat, base, desc = i.get("category") or "", i.get("base") or "", i.get("description") or ""
    src = "kraken:category=%s" % cat
    if cat == "Forex":
        return "fx", src, "pair %s/%s" % (base, i.get("quote", ""))
    if cat == "Indices":
        return "index", src, desc
    if cat == "Commodities":
        if base in KR_METAL:
            return "metal", src + "+ticker-table", desc
        if base in KR_ENERGY:
            return "energy", src + "+ticker-table", desc
        return "commodity", src, desc
    if cat == "xStocks":
        und = base[:-1] if base.endswith("x") else base
        if base in KR_XSTOCK_ETF:
            return "etf", src + "+ticker-table", "xStock (tokenized), underlying=%s" % und
        if base == "SPCXx":
            return "other", src, "xStock; underlying SPCX not identified from metadata (equity or ETF?)"
        return "equity", src, "xStock (tokenized), underlying=%s" % und
    if cat == "Equities":
        if re.search(r"\bETF\b|\b\d+x\b", desc):
            return "etf", src + "+description", desc
        return "equity", src, desc
    if cat == "Pre-IPO":
        return "equity", src, "pre-IPO private company; " + desc
    if cat == "Stablecoin":
        if base in KR_GOLD_TOKENS:
            return "metal", src + "+ticker-table", "gold-backed token (exchange lists it under Stablecoin)"
        if base in KR_STABLES:
            return "crypto", src, "stablecoin"
        return "other", src, "category Stablecoin, ticker %s not in table" % base
    if cat in KR_CRYPTO_CATS:
        return "crypto", src, ""
    return "other", src, "category %r not mapped" % cat


def kraken_instruments(http, log):
    d = http.get_json(KR + "/derivatives/api/v3/instruments")
    if d.get("result") != "success":
        raise RuntimeError("instruments: %s" % str(d)[:200])
    fees = {}
    try:                                                    # базовый тариф (tier usdVolume=0), % -> bps
        fs = http.get_json(KR + "/derivatives/api/v3/feeschedules")
        for s in fs.get("feeSchedules", []):
            tiers = sorted(s.get("tiers", []), key=lambda t: t.get("usdVolume", 0))
            if tiers:
                fees[s["uid"]] = (tiers[0]["takerFee"] * 100, tiers[0]["makerFee"] * 100)
    except Exception as e:
        log("feeschedules не получены: %s" % e)
    out = []
    for i in d["instruments"]:
        sym = i["symbol"]
        perp = sym.startswith(("PF_", "PI_"))
        dated = sym.startswith(("FF_", "FI_")) or bool(i.get("lastTradingTime"))
        kind = "perp" if perp and not dated else "dated"
        cls, csrc, cnote = kraken_class(i)
        status = "Trading" if i.get("tradeable") and not i.get("suspended") else ("Suspended" if i.get("suspended") else "NotTradeable")
        if i.get("isExpired"):
            status = "Expired"
        t, m = fees.get(i.get("feeScheduleUid"), (None, None))
        notes = [cnote, "type=" + i.get("type", "")]
        if dated:
            notes.append("dated future (not perpetual), expiry=%s" % i.get("lastTradingTime"))
        if sym.startswith("PI_"):
            notes.append("inverse: contracts are USD; v approximated as contracts/close")
        if i.get("minRelativeFundingRate") is not None:
            notes.append("funding clamp [%s,%s]/h" % (i.get("minRelativeFundingRate"), i.get("maxRelativeFundingRate")))
        out.append({
            "exchange": "kraken", "symbol": sym, "native_id": sym, "base": i.get("base", ""), "quote": i.get("quote", "USD"),
            "settle": "USD", "class": cls, "class_src": csrc, "status": status, "contract_size": i.get("contractSize", ""),
            "tick_size": i.get("tickSize", ""), "listed_ms": iso_ms(i["openingDate"]) if i.get("openingDate") else "",
            "delisted_ms": iso_ms(i["lastTradingTime"]) if i.get("lastTradingTime") else "",
            "funding_interval_h": "" if dated else 1,
            "taker_fee_bps": "" if t is None else round(t, 4), "maker_fee_bps": "" if m is None else round(m, 4),
            "note": add_note(*notes), "_kind": kind,
        })
    return out


# ───────────────────────────── KRAKEN: данные ─────────────────────────────
def kraken_candles(http, sym, start_ms, eff_end_ms, csize, inverse, keep_filler):
    """-> (rows, n_requests, n_filler_dropped). Окно [start, eff_end); `to` у биржи включительно."""
    rows, nreq, dropped = [], 0, 0
    cur = (start_ms + 999) // 1000
    last_s = (eff_end_ms - MIN_MS) // 1000
    while cur <= last_s:
        d = http.get_json(KR + "/api/charts/v1/trade/%s/1m" % sym, {"from": cur, "to": last_s})
        nreq += 1
        c = d.get("candles") or []
        for x in c:
            t = int(x["time"])
            vol = float(x["volume"])
            if vol == 0 and not keep_filler:
                dropped += 1
                continue
            if inverse:
                cl = float(x["close"])
                v, qv = (num(vol / cl) if cl else "0"), num(vol)
            else:
                v, qv = num(vol * csize), None
            rows.append((t, x["open"], x["high"], x["low"], x["close"], v, qv))
        if not c or not d.get("more_candles"):
            break
        nxt = int(c[-1]["time"]) // 1000 + 60
        if nxt <= cur:
            break
        cur = nxt
    return rows, nreq, dropped


def kraken_funding(http, sym, start_ms, end_ms):
    d = http.get_json(KR + "/derivatives/api/v4/historicalfundingrates", {"symbol": sym})
    if d.get("result") != "success":
        raise RuntimeError("funding %s: %s" % (sym, str(d)[:200]))
    rows = []
    for r in d.get("rates", []):
        t = iso_ms(r["timestamp"])
        if start_ms <= t < end_ms:
            rows.append((t, repr(float(r["relativeFundingRate"])), 1))
    return rows


def do_kraken(ctx, ins):
    sym, name = ins["symbol"], ins["_file"]
    csize = float(ins["contract_size"] or 1)
    rows, nreq, dropped = kraken_candles(ctx["http"], sym, ctx["start"], ctx["eff_end"], csize, sym.startswith("PI_"), ctx["keep_filler"])
    nc, gaps = write_candles(os.path.join(ctx["dir"], "candles", name + ".csv.gz"), rows, MIN_MS, ctx["start"], ctx["end"])
    nf = 0
    if ins["_kind"] == "perp":
        frows = kraken_funding(ctx["http"], sym, ctx["start"], ctx["end"])
        nf = write_funding(os.path.join(ctx["dir"], "funding", name + ".csv.gz"), frows)
        nreq += 1
    info = "candles=%d minutes_without_trades=%d filler_dropped=%d funding=%d requests=%d" % (nc, gaps, dropped, nf, nreq)
    mark_done(ctx["out"], ctx["ex"], name, info)
    return info


# ───────────────────────────── LIGHTER: классификация ─────────────────────────────
# strategy_index — недокументированное поле orderBookDetails; по факту это группа рынка у биржи:
#   2 крипта · 3 сырьё · 4 валюта · 5 акции/ETF/индексы США · 6 акции Азии/ETF · 7 «прочее» (пре-IPO, предрынок, ставки) · 0 устаревшие.
LT_METAL = {"XAU", "XAG", "XPT", "XPD", "XCU"}
LT_ENERGY = {"WTI", "BRENTOIL", "NATGAS"}
LT_COMMODITY = {"WHEAT"}
LT_GOLD_TOKENS = {"PAXG", "XAUT"}
LT_ETF = {"SPY", "QQQ", "IWM", "DIA", "SOXX", "SOXL", "SOXS", "BOTZ", "MAGS", "URA", "KORU", "EWY", "DRAM"}   # таблица по тикеру
LT_INDEX = {"US100", "US500", "KRCOMP"}
LT_PREIPO = {"OPENAI", "ANTHROPIC", "OURA"}
LT_EQUITY_LEGACY = {"SKHYNIX", "SAMSUNG", "HYUNDAI", "HANMI", "SPACEX"}          # strategy_index=0
LT_CRYPTO_LEGACY = {"LAUNCHCOIN", "1000TOSHI", "AI16Z", "BIRB", "DUSK", "YZY", "MKR", "PIPPIN"}
LT_SPOT_CRYPTO = {"ETH", "LINK", "UNI", "LDO", "SKY", "AAVE", "LIT", "AZTEC", "weETH"}
LT_SPOT_ETF = {"rhSPY", "rhQQQ"}
LT_FX_CCY = {"EUR", "GBP", "USD", "JPY", "CHF", "CAD", "AUD", "NZD", "KRW", "HKD", "CNH", "SGD", "MXN"}


def lighter_class(sym, base, mtype, si):
    """-> (class, class_src, note)."""
    src = "lighter:strategy_index=%s" % si
    if mtype == "spot":
        if base in LT_SPOT_CRYPTO:
            return "crypto", "spot-table", "spot"
        if base in LT_SPOT_ETF:
            return "etf", "spot-table", "spot; tokenized ETF (rebasing)"
        if base in LT_GOLD_TOKENS:
            return "metal", "spot-table", "spot; gold-backed token"
        return "other", "spot-table", "spot; base not in table"
    if sym in LT_GOLD_TOKENS:
        return "metal", src + "+ticker-table", "gold-backed token (exchange group: crypto)"
    if si == 4 or (len(sym) == 6 and sym[:3] in LT_FX_CCY and sym[3:] in LT_FX_CCY):
        return "fx", src, "pair %s/%s" % (sym[:3], sym[3:])
    if sym in LT_METAL:
        return "metal", src + "+ticker-table", ""
    if sym in LT_ENERGY:
        return "energy", src + "+ticker-table", ""
    if sym in LT_COMMODITY:
        return "commodity", src + "+ticker-table", ""
    if sym == "H100":
        return "other", src, "Nvidia H100 GPU rental price index (neither commodity nor equity)"
    if sym == "US10Y":
        return "other", src, "US 10y Treasury (price ~98, rates product)"
    if sym in LT_INDEX:
        return "index", src + "+ticker-table", ""
    if sym in LT_ETF:
        return "etf", src + "+ticker-table", ""
    if sym in LT_PREIPO:
        return "equity", src + "+ticker-table", "pre-IPO private company (premium multiplier 1)"
    if si in (5, 6) or sym in LT_EQUITY_LEGACY:
        return "equity", src + ("+ticker-table" if sym in LT_EQUITY_LEGACY else ""), ""
    if si == 2 or sym in LT_CRYPTO_LEGACY:
        return "crypto", src + ("+ticker-table" if sym in LT_CRYPTO_LEGACY else ""), ("SPX6900 memecoin, NOT the S&P index (price ~0.34)" if sym == "SPX" else "")
    return "other", src, "underlying not identified from exchange metadata (strategy_index=%s)" % si


def lighter_is_prediction(o):
    s = o.get("symbol", "")
    return ("UPDOWN" in s.upper() or bool(re.search(r"-\d{9,}$", s)) or int(o.get("outcome") or 0) != 0
            or int(o.get("start_timestamp") or 0) > 0 or int(o.get("end_timestamp") or 0) > 0 or int(o.get("settlement_type") or 0) != 0)


def lighter_get(http, path, params):
    """Как http.get_json, но с повтором при ошибке в теле JSON (code != 200, напр. 429)."""
    last = None
    for attempt in range(6):
        d = http.get_json(LT + path, params)
        code = d.get("code", 200) if isinstance(d, dict) else 200
        if code == 200:
            return d
        last = d
        time.sleep(min(5 * (attempt + 1), 30))
    raise RuntimeError("%s code=%s: %s" % (path, last.get("code"), str(last)[:200]))


def lighter_instruments(http, log):
    det = {}
    try:
        d = lighter_get(http, "/api/v1/orderBookDetails", {})
        for o in (d.get("order_book_details") or []) + (d.get("spot_order_book_details") or []):
            det[o["market_id"]] = o
    except Exception as e:
        log("orderBookDetails не получен: %s (классы по таблицам тикеров)" % e)
    books = {}
    d = lighter_get(http, "/api/v1/orderBooks", {})
    for o in d.get("order_books") or []:
        books[o["market_id"]] = o
    merged = {}
    for mid in set(det) | set(books):
        o = dict(books.get(mid, {}))
        o.update(det.get(mid, {}))
        merged[mid] = o
    out = []
    for mid in sorted(merged):
        o = merged[mid]
        sym, mtype = o["symbol"], o.get("market_type", "perp")
        base = sym.split("/")[0] if mtype == "spot" else sym
        quote = sym.split("/")[1] if mtype == "spot" else "USD"
        si = o.get("strategy_index")
        pred = lighter_is_prediction(o)
        if pred:
            cls, csrc, cnote = "other", "lighter:pattern(UPDOWN/outcome/timestamps)", "prediction"
            kind = "prediction"
        else:
            cls, csrc, cnote = lighter_class(sym, base, mtype, si)
            kind = "spot" if mtype == "spot" else "perp"
        if cls == "fx" and len(sym) == 6:
            base, quote = sym[:3], sym[3:]
        notes = [cnote, "market_id=%s" % mid]
        if mtype == "perp" and re.match(r"^1000[A-Z]", sym):
            notes.append("multiplier 1000: price and size per 1000 %s" % sym[4:])
        if o.get("is_frozen"):
            notes.append("frozen")
        if o.get("market_deployer") and o["market_deployer"] != "lighter":
            notes.append("deployer=%s" % o["market_deployer"])
        if float(o.get("multiplier") or 1) != 1.0:
            notes.append("multiplier=%s" % o["multiplier"])
        if si is None:
            notes.append("no strategy_index (details unavailable)")
        pd = o.get("price_decimals", o.get("supported_price_decimals"))
        zero_fee = all(float(o.get(k) or 0) == 0 for k in ("taker_fee", "maker_fee"))
        out.append({
            "exchange": "lighter", "symbol": sym, "native_id": mid, "base": base, "quote": quote, "settle": "USDC",
            "class": cls, "class_src": csrc, "status": o.get("status", ""), "contract_size": 1,
            "tick_size": ("%g" % (10 ** -int(pd))) if pd is not None else "",
            "listed_ms": o.get("created_at", ""), "delisted_ms": "", "funding_interval_h": 1 if mtype == "perp" else "",
            "taker_fee_bps": 0 if zero_fee else "", "maker_fee_bps": 0 if zero_fee else "",
            "note": add_note(*notes), "_kind": kind, "_mid": mid,
        })
    return out


def lighter_candles(http, mid, start_ms, eff_end_ms, keep_filler):
    """Окно [start, eff_end). Идём НАЗАД: end = время первой полученной свечи (см. докстринг). -> (rows, n_requests, dropped)."""
    S = (start_ms + 999) // 1000
    end = eff_end_ms // 1000
    got, nreq = {}, 0
    while end > S:
        d = lighter_get(http, "/api/v1/candles", {"market_id": mid, "resolution": "1m", "start_timestamp": S,
                                                    "end_timestamp": end, "count_back": 0})
        nreq += 1
        c = d.get("c") or []
        if not c:
            break
        first = min(int(x["t"]) for x in c) // 1000
        for x in c:
            got[int(x["t"])] = x
        if first <= S or first >= end:
            break
        end = first
    rows, dropped = [], 0
    for t in sorted(got):
        x = got[t]
        vol = float(x.get("v") or 0)
        if (vol == 0 or "O" in x) and not keep_filler:
            dropped += 1
            continue
        rows.append((t, repr(x["o"]), repr(x["h"]), repr(x["l"]), repr(x["c"]), num(vol), num(x.get("V") or 0, 8)))
    return rows, nreq, dropped


def lighter_funding(http, mid, start_ms, end_ms):
    S, E = (start_ms + 999) // 1000, end_ms // 1000
    step = 700 * 3600
    rows, nreq, cur = [], 0, S
    while cur < E:
        e = min(E, cur + step)
        # фильтр биржи: строка t отдаётся при start+3600 <= t < end (start «сдвинут» на час) -> просим start = cur-3600
        d = lighter_get(http, "/api/v1/fundings", {"market_id": mid, "resolution": "1h", "start_timestamp": cur - 3600,
                                                     "end_timestamp": e, "count_back": 0})
        nreq += 1
        for r in d.get("fundings") or []:
            t = int(r["timestamp"]) * 1000
            if not (start_ms <= t < end_ms):
                continue
            sign = -1.0 if r.get("direction") == "short" else 1.0
            rows.append((t, repr(round(sign * float(r["rate"]) / 100.0, 12)), 1))
        cur = e
    return rows, nreq


def do_lighter(ctx, ins):
    mid, name = ins["_mid"], ins["_file"]
    rows, nreq, dropped = lighter_candles(ctx["http"], mid, ctx["start"], ctx["eff_end"], ctx["keep_filler"])
    nc, gaps = write_candles(os.path.join(ctx["dir"], "candles", name + ".csv.gz"), rows, MIN_MS, ctx["start"], ctx["end"])
    nf = 0
    if ins["_kind"] == "perp":
        frows, fr = lighter_funding(ctx["http"], mid, ctx["start"], ctx["end"])
        nf = write_funding(os.path.join(ctx["dir"], "funding", name + ".csv.gz"), frows)
        nreq += fr
    info = "candles=%d minutes_without_trades=%d filler_dropped=%d funding=%d requests=%d" % (nc, gaps, dropped, nf, nreq)
    mark_done(ctx["out"], ctx["ex"], name, info)
    return info


# ───────────────────────────── main ─────────────────────────────
def main():
    ap = common_args("Kraken Futures / Lighter: минутные свечи и фандинг за окно (публичные API); --rate по умолчанию: "
                     "kraken 3, lighter 2 (выше ~2/с Lighter отдаёт 429)", default_rate=None, default_workers=2)
    ap.add_argument("--exchange", required=True, choices=["kraken", "lighter"])
    ap.add_argument("--include-dated", action="store_true", help="Kraken: качать и датированные FF_/FI_ (по умолчанию только перпетуалы)")
    ap.add_argument("--include-spot", action="store_true", help="Lighter: качать и спот-рынки (по умолчанию только перпы)")
    ap.add_argument("--keep-filler", action="store_true", help="писать и «дорисованные» биржей пустые минуты (v=0); по умолчанию — нет")
    a = ap.parse_args()
    ex = a.exchange
    start, end = parse_ts(a.start), parse_ts(a.end)
    if end <= start:
        ap.error("--end должен быть позже --start")
    ensure_dirs(a.out, ex)
    log = make_logger(a.out, ex, "run")
    rate = a.rate if a.rate else (3.0 if ex == "kraken" else 2.0)
    http = Http(rate=rate)
    log("%s окно [%s, %s) rate=%s workers=%s" % (ex, a.start, a.end, rate, a.workers))

    inst = kraken_instruments(http, log) if ex == "kraken" else lighter_instruments(http, log)
    # имена файлов; коллизии safe_name разводим суффиксом
    seen = {}
    for i in inst:
        n = safe_name(i["symbol"])
        if n in seen:
            n = "%s_%s" % (n, i["native_id"])
        seen[n] = 1
        i["_file"] = n
    write_instruments(a.out, ex, inst)
    cnt = {}
    for i in inst:
        k = (i["_kind"], i["class"])
        cnt[k] = cnt.get(k, 0) + 1
    log("instruments.csv: %d строк; по (вид, класс): %s" % (len(inst), json.dumps({"%s/%s" % k: v for k, v in sorted(cnt.items())}, ensure_ascii=False)))
    if a.list_only:
        return 0

    want = {s.strip() for s in a.symbols.split(",") if s.strip()}
    if want:
        todo = [i for i in inst if i["symbol"] in want and i["_kind"] != "prediction"]
        miss = want - {i["symbol"] for i in todo}
        if miss:
            log("НЕТ в списке (или prediction): %s" % ",".join(sorted(miss)))
    else:
        todo = [i for i in inst if i["_kind"] == "perp"
                or (i["_kind"] == "dated" and a.include_dated) or (i["_kind"] == "spot" and a.include_spot)]
    if a.limit_symbols:
        todo = todo[:a.limit_symbols]
    if not a.force:
        left = [i for i in todo if not is_done(a.out, ex, i["_file"])]
        log("к закачке %d из %d (остальные с меткой .done)" % (len(left), len(todo)))
        todo = left
    eff_end = min(end, (now_ms() // MIN_MS) * MIN_MS)          # текущая незакрытая минута не пишется
    ctx = {"http": http, "out": a.out, "ex": ex, "dir": os.path.join(a.out, ex), "start": start, "end": end,
           "eff_end": eff_end, "keep_filler": a.keep_filler}
    impl = do_kraken if ex == "kraken" else do_lighter

    def fn(i):
        try:
            return impl(ctx, i)
        except Exception as e:                      # в сообщении об ошибке — символ (run_parallel режет repr словаря)
            raise RuntimeError("%s: %s" % (i["symbol"], e))
    res = run_parallel(todo, fn, a.workers, log)
    log("HTTP: запросов=%d повторов=%d ошибок=%s" % (http.stats["req"], http.stats["retry"], json.dumps(http.stats["http_err"])))
    for s, e in res["fails"]:
        log("СБОЙ %s: %s" % (s, e))
    return 1 if res["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
