#!/usr/bin/env python3
"""BingX: закачка публичных минутных свечей и истории фандинга ВСЕХ перпетуалов (крипта + TradFi-перпы: акции, ETF, индексы,
металлы, энергия, FX) за окно [start, end) UTC. Только публичные эндпоинты, ключей нет. Формат результата — xdl_common.

Эндпоинты (https://open-api.bingx.com):
  GET /openApi/swap/v2/quote/contracts      — список контрактов (только действующие и приостановленные; снятых за окно в нём нет)
  GET /openApi/swap/v2/quote/premiumIndex   — без symbol отдаёт все контракты сразу: fundingIntervalHours (текущий интервал)
  GET /openApi/swap/v3/quote/klines         — свечи; ответ ПО УБЫВАНИЮ времени, limit<=1440 берётся с НОВОГО конца окна,
                                              при заданных startTime+endTime окно [start, end) (end исключительно);
                                              поле time = время ОТКРЫТИЯ свечи (мс), volume — в базовых единицах, quote-оборота нет
  GET /openApi/swap/v2/quote/fundingRate    — фандинг; ответ по убыванию, limit<=1000, startTime/endTime ОБА включительно

Особенности (см. отчёт): HTTP всегда 200, ошибки — в JSON `code`; 100410 «请稍后重试» — временный отказ (повтор);
109415 — контракт приостановлен (свечей нет); 109425 — нет такого символа. Ответ свечей сжимается gzip (в 1,5 раза быстрее).

Запуск (проба):  python3 bingx.py --out OUT --start 2026-10-01 --end 2026-10-03 --symbols BTC-USDT,ETH-USDT --rate 3 --workers 2
Полный прогон:   python3 bingx.py --out /data/xdl --rate 8 --workers 6
"""
import os, sys, re, json, gzip, time, random, urllib.request, urllib.parse, urllib.error
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xdl_common as X

EX = "bingx"
BASE = "https://open-api.bingx.com"
P_CONTRACTS = "/openApi/swap/v2/quote/contracts"
P_PREMIUM = "/openApi/swap/v2/quote/premiumIndex"
P_KLINES = "/openApi/swap/v3/quote/klines"
P_FUNDING = "/openApi/swap/v2/quote/fundingRate"
KL_LIMIT = 1440
FR_LIMIT = 1000
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
FUND_LOOKBACK_MS = DAY_MS            # запас назад, чтобы у первой выплаты окна был известен интервал по разнице времён
LAUNCH_SLACK_MS = 2 * DAY_MS         # нижняя граница свечей = launchTime - 2 суток (запас на неточность поля)
RETRY_CODES = {100410, 100500, 100001, 100004, 100008}   # временные отказы бэкенда внутри HTTP 200
OK_INTERVALS = {0.5, 1, 2, 4, 6, 8, 12, 24}


class Paused(Exception):
    pass


class NotExist(Exception):
    pass


class BxHttp(X.Http):
    """Http из xdl_common + разбор JSON-кода BingX + gzip + учёт остатка лимита из заголовков."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.stats.update({"code": {}, "min_remain": None, "gz": 0})

    def _penalty(self, sec):
        with self.rl.lock:
            self.rl.next = max(self.rl.next, time.monotonic() + sec)

    def call(self, path, params=None):
        url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
        h = {"User-Agent": self.ua, "Accept": "application/json", "Accept-Encoding": "gzip"}
        last = None
        for attempt in range(self.retries):
            self.rl.wait()
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=self.timeout) as r:
                    raw = r.read()
                    gz = r.headers.get("Content-Encoding") == "gzip"
                    remain = r.headers.get("X-RateLimit-Requests-Remain")
                if gz:
                    raw = gzip.decompress(raw)
                j = json.loads(raw)
                with self.lock:
                    self.stats["req"] += 1
                    self.stats["gz"] += 1 if gz else 0
                    if remain is not None and remain.isdigit():
                        m = self.stats["min_remain"]
                        self.stats["min_remain"] = int(remain) if m is None else min(m, int(remain))
            except urllib.error.HTTPError as e:
                last = e
                with self.lock:
                    self.stats["http_err"][e.code] = self.stats["http_err"].get(e.code, 0) + 1
                    self.stats["retry"] += 1
                if e.code == 404:
                    raise
                sl = (2 ** attempt) * 0.5 + random.random()
                if e.code in (418, 429):
                    sl = max(sl, 5 * (attempt + 1))
                    self._penalty(sl)
                time.sleep(min(sl, 60))
                continue
            except Exception as e:                       # сеть / таймаут / разбор
                last = e
                with self.lock:
                    self.stats["retry"] += 1
                time.sleep(min((2 ** attempt) * 0.5 + random.random(), 30))
                continue
            code = j.get("code") if isinstance(j, dict) else None
            if code == 0:
                return j
            with self.lock:
                self.stats["code"][code] = self.stats["code"].get(code, 0) + 1
            if code == 109415:
                raise Paused(str(j.get("msg"))[:100])
            if code == 109425:
                raise NotExist(str(j.get("msg"))[:100])
            if code in RETRY_CODES or code is None:
                last = "code=%s %s" % (code, str(j.get("msg") if isinstance(j, dict) else j)[:80])
                with self.lock:
                    self.stats["retry"] += 1
                sl = 1.0 + attempt * 1.5 + random.random()
                self._penalty(sl)                         # общий откат для всех потоков
                time.sleep(sl)
                continue
            raise RuntimeError("BingX code=%s msg=%s | %s" % (code, str(j.get("msg"))[:120], url[:140]))
        raise RuntimeError("запрос не удался после %d попыток: %s | %s" % (self.retries, url[:140], last))


# --------------------------------------------------------------------------------------------------------------------------
# классификация
# --------------------------------------------------------------------------------------------------------------------------
# Префиксы TradFi-перпов BingX (нативный символ): NCSK — акции (stock), NCSI — индексы (stock index), NCCO — сырьё (commodity),
# NCFX — валюта (forex). Всё без префикса NC — крипто. ETF внутри NCSK/NCSI на бирже НЕ помечены — отделяются ручным списком
# тикеров (class_src=ticker_list); список составлен по общеизвестным тикерам и может быть неполным.
ETF_TICKERS = set("""
SPY QQQ IWM DIA SPYM VGT VUG VGK VWO VFH IYF EFA EEM IEMG INDA FXI KWEB EWZ EWT EWY EWJ FLKR EWZS EIDO ARGT KRE XBI IBB ARKK ARKG
ITA XME URA COPX URNM REMX GDX GDXJ SIL SILJ SLV GLD UGL AGQ GSG USO UCO BOIL OIH XOP XLE XLF XLK XLU XLB XLP XLI XLY XLV IGV SOXX
SMH SOXQ CIBR BUG BOTZ MAGS AIQ FBTC IBIT BITO BITX IJR SCHD LQD HYG USHY TLT IEF SGOV BIL TMF TBT UVXY TQQQ SQQQ SOXL SOXS TNA TZA
UPRO SPXL UDOW FNGU NUGT KORU LABU SHLD DRAM BWET KODEX200 USD
TSLL NVDL MSTX MSTU MSTZ AMZU MSFU AAPU METU GGLL CONL AMDL MUU MVLL SNXX CRCG CRCD CRWL CRWG PLTU SMCX COHX LITX AVGX RKLX CRMG
OKTG AAOX ORCX NVD
""".split())
PRIVATE_COS = {"ANTHROPIC", "OPENAI", "POLYMARKET"}       # частные компании (синтетические перпы до IPO) — не акции
# тикеры, похожие на плечевые/закрытые фонды (по смыслу и по соседству с базовыми акциями на бирже), но не подтверждённые — class=other
AMBIG_FUNDS = {"MUD", "SKUU", "SKDD", "STXX", "SNDQ", "SSPC", "NBIL", "DXYZ", "VCX"}
OTHER_INDEX = {"H100": "GPU-compute price index (NVIDIA H100)", "B200": "GPU-compute price index (NVIDIA B200)"}
ENERGY_KEYS = ("HEATINGOIL", "OILBRENT", "OILWTI", "NATGAS")
METAL_KEYS = ("GOLD", "XAU", "XAG", "XPT", "PALLADIUM", "NICKEL", "ZINC", "LEAD", "ALUMINIUM", "COPPER")
MULT_RE = re.compile(r"^(1(?:0{3}|0{4}|0{6}|0{7}|0{8}|0{9}))(?=[A-Z])")
SK_RE = re.compile(r"^NCSK(.+?)(?:2USD|USDT|2[A-Z]{3})-(?:USDT|USDC)$")


def _stem(sym, prefix):
    """Нативный символ без префикса NC.. и хвоста '2USD-USDT' / 'USD-USDT'."""
    s = sym[len(prefix):]
    s = re.sub(r"-(USDT|USDC)$", "", s)
    for suf in ("2USD", "USDT", "USD"):
        if s.endswith(suf) and len(s) > len(suf):
            return s[:-len(suf)]
    return s


def classify(c):
    """c — запись /contracts. Возвращает dict(class, class_src, base, quote, note_parts)."""
    sym, asset, disp = c["symbol"], c.get("asset", ""), (c.get("displayName") or "").replace("-USDT", "").replace("-USDC", "").strip()
    settle = c.get("currency", "")
    notes = []
    if not sym.startswith("NC"):
        base = asset or sym.rsplit("-", 1)[0]
        if base != sym.rsplit("-", 1)[0]:
            notes.append("asset!=symbol:%s" % asset)
        m = MULT_RE.match(base)
        if m:
            notes.append("multiplier=%s (цена и объём в единицах 1000/…-кратной монеты, как у биржи)" % m.group(1))
        if base in ("PAXG", "XAUT"):
            notes.append("tokenized gold (крипто-токен, привязан к золоту)")
        return dict(cls="crypto", src="symbol_prefix:none(non-NC)", base=base, quote=settle, notes=notes)
    pre = sym[:4]
    stem = _stem(sym, pre)
    if pre == "NCSK":
        m = SK_RE.match(sym)
        if m:
            stem = m.group(1)
        base = disp
        if disp.endswith("US") and disp[:-2] == stem:      # 'AMDUS' -> 'AMD' (суффикс US биржа ставит против коллизии с крипто-тикером)
            base = stem
        if base != stem:
            notes.append("stem=%s" % stem)
        if stem in PRIVATE_COS or base in PRIVATE_COS:
            return dict(cls="other", src="ticker_list", base=base, quote="USD",
                        notes=notes + ["частная компания, синтетический перп до IPO (не листингованная акция)"])
        if stem in AMBIG_FUNDS or base in AMBIG_FUNDS:
            return dict(cls="other", src="ticker_list", base=base, quote="USD",
                        notes=notes + ["неоднозначно: похоже на ETF/фонд/плечевой продукт, не подтверждено — проверить вручную"])
        if stem in ETF_TICKERS or base in ETF_TICKERS:
            return dict(cls="etf", src="ticker_list", base=base, quote="USD", notes=notes)
        return dict(cls="equity", src="symbol_prefix:NCSK(ETF-список ручной, возможны неотделённые ETF/фонды)", base=base, quote="USD", notes=notes)
    if pre == "NCSI":
        base = stem
        if stem in OTHER_INDEX:
            return dict(cls="other", src="ticker_list", base=base, quote="USD", notes=notes + [OTHER_INDEX[stem]])
        if stem in ETF_TICKERS:
            return dict(cls="etf", src="ticker_list", base=base, quote="USD", notes=notes + ["страновой ETF внутри группы NCSI"])
        if stem == "DXY":
            notes.append("индекс доллара (корзина валют)")
        notes.append("display=%s" % disp)
        return dict(cls="index", src="symbol_prefix:NCSI", base=base, quote="USD", notes=notes)
    if pre == "NCFX":
        pair = disp.replace(" ", "")
        if re.fullmatch(r"[A-Z]{6}", pair):
            return dict(cls="fx", src="symbol_prefix:NCFX", base=pair[:3], quote=pair[3:], notes=notes + ["pair=%s" % pair])
        return dict(cls="fx", src="symbol_prefix:NCFX", base=stem, quote="USD", notes=notes + ["display=%s" % disp])
    if pre == "NCCO":
        d = disp.replace(" ", "")
        u = stem.upper()
        mm = re.fullmatch(r"(XAU|XAG)([A-Z]{3})", d.upper())
        if mm:
            kind = "ratio" if mm.group(2) in ("XAG", "XPT") else "cross"
            return dict(cls="metal", src="name_rule", base=mm.group(1), quote=mm.group(2),
                        notes=notes + ["%s: %s в %s" % (kind, mm.group(1), mm.group(2))])
        pm = re.search(r"\(([A-Z]{2,4})\)", disp)
        base = pm.group(1) if pm else (u if not re.match(r"^\d", u) else re.sub(r"^\d+", "", u))
        if any(k in u for k in ENERGY_KEYS):
            return dict(cls="energy", src="name_rule", base=base, quote="USD", notes=notes + ["display=%s" % disp])
        if any(k in u for k in METAL_KEYS):
            return dict(cls="metal", src="name_rule", base=base, quote="USD", notes=notes + ["display=%s" % disp])
        return dict(cls="commodity", src="symbol_prefix:NCCO(default)", base=base, quote="USD", notes=notes + ["display=%s" % disp])
    return dict(cls="other", src="unknown_prefix", base=stem, quote="USD", notes=notes + ["неизвестный префикс %s" % pre])


STATUS_NAMES = {1: "Trading", 25: "Paused", 0: "Offline", 5: "PreOnline"}


def fmt_g(x):
    return ("%.10g" % x) if x is not None else ""


def fetch_instruments(http, log):
    j = http.call(P_CONTRACTS)
    cons = j.get("data") or []
    prem = {}
    try:
        pj = http.call(P_PREMIUM)
        for x in pj.get("data") or []:
            prem[x["symbol"]] = x.get("fundingIntervalHours")
    except Exception as e:
        log("premiumIndex недоступен (%s) — funding_interval_h только по данным" % str(e)[:100])
    out = []
    for c in cons:
        k = classify(c)
        pp = c.get("pricePrecision")
        tick = format(Decimal(1).scaleb(-int(pp)), "f") if pp is not None else ""
        st = int(c.get("status", -1))
        notes = list(k["notes"])
        notes.append("qty_step=%s" % c.get("size"))
        notes.append("min_notional_usdt=%s" % c.get("tradeMinUSDT"))
        if str(c.get("apiStateOpen")).lower() != "true":
            notes.append("api_open=false")
        if str(c.get("apiStateClose")).lower() != "true":
            notes.append("api_close=false")
        if c.get("maintainTime"):
            notes.append("maintainTime=%s" % c["maintainTime"])
        if st == 25:
            notes.append("приостановлен: klines отдают code 109415, истории в окне через API нет")
        tfee, mfee = c.get("takerFeeRate"), c.get("makerFeeRate")
        out.append({
            "exchange": EX, "symbol": c["symbol"], "native_id": c.get("contractId", ""), "base": k["base"], "quote": k["quote"],
            "settle": c.get("currency", ""), "class": k["cls"], "class_src": k["src"], "status": STATUS_NAMES.get(st, "status_%s" % st),
            "contract_size": 1,                       # volume в klines уже в базовых единицах; 'size' биржи — шаг количества, не множитель
            "tick_size": tick, "listed_ms": c.get("launchTime") or "", "delisted_ms": c.get("offTime") or "",
            "funding_interval_h": prem.get(c["symbol"], ""),
            "taker_fee_bps": fmt_g(float(tfee) * 1e4) if tfee is not None else "",
            "maker_fee_bps": fmt_g(float(mfee) * 1e4) if mfee is not None else "",
            "note": "; ".join(notes),
        })
    return out


# --------------------------------------------------------------------------------------------------------------------------
# закачка одного контракта
# --------------------------------------------------------------------------------------------------------------------------
def candle_bounds(inst, start_ms, end_eff):
    a = start_ms
    ln = inst.get("listed_ms")
    if ln not in ("", None) and int(ln) > 0:
        a = max(a, ((int(ln) - LAUNCH_SLACK_MS) // X.MIN_MS) * X.MIN_MS)
    return a, end_eff


def fetch_candles(http, sym, a, b):
    """Свечи [a, b): окна по 1440 минут; ответ по убыванию. Защита: если пришло ровно limit штук и самая старая новее начала окна —
    докачиваем хвост [t, min_time)."""
    rows, reqs = [], 0
    t = a
    while t < b:
        e = min(t + KL_LIMIT * X.MIN_MS, b)
        lo, hi = t, e
        while lo < hi:
            j = http.call(P_KLINES, {"symbol": sym, "interval": "1m", "startTime": lo, "endTime": hi, "limit": KL_LIMIT})
            reqs += 1
            d = j.get("data") or []
            if not d:
                break
            for k in d:
                rows.append((int(k["time"]), k["open"], k["high"], k["low"], k["close"], k["volume"], None))
            tmin = min(int(k["time"]) for k in d)
            if len(d) >= KL_LIMIT and tmin > lo:
                hi = tmin              # старый конец окна обрезан лимитом — повторяем для [lo, tmin)
                continue
            break
        t = e
    return rows, reqs


def interval_str(h):
    if abs(h - round(h)) < 1e-9:
        return str(int(round(h)))
    return "%.4g" % h


def fetch_funding(http, sym, start_ms, end_ms, prem_h):
    """Фандинг [start, end); возвращает ([(t, rate, interval_h)], число запросов, число выплат до окна)."""
    f_lo = start_ms - FUND_LOOKBACK_MS
    cur_end = end_ms - 1                                   # endTime включительно
    recs, reqs = {}, 0
    while True:
        j = http.call(P_FUNDING, {"symbol": sym, "startTime": f_lo, "endTime": cur_end, "limit": FR_LIMIT})
        reqs += 1
        d = j.get("data") or []
        if not d:
            break
        for x in d:
            recs[int(x["fundingTime"])] = x["fundingRate"]
        if len(d) < FR_LIMIT:
            break
        cur_end = min(int(x["fundingTime"]) for x in d) - 1
        if cur_end < f_lo:
            break
    ts = sorted(recs)
    fb = None
    try:
        fb = float(prem_h) if prem_h not in ("", None) else None
    except ValueError:
        fb = None
    rows = []
    for i, t in enumerate(ts):
        if t < start_ms or t >= end_ms:
            continue
        h = None
        if i > 0:
            h = (t - ts[i - 1]) / HOUR_MS
        elif i + 1 < len(ts):
            h = (ts[i + 1] - t) / HOUR_MS
        if h is None or round(h * 2) / 2 not in OK_INTERVALS or abs(h * 2 - round(h * 2)) > 1e-6:
            h = fb
        rows.append((t, recs[t], interval_str(h) if h is not None else ""))
    return rows, reqs


def ohlc_check(rows):
    bad = nonpos = zero_v = 0
    for r in rows:
        o, h, l, c = float(r[1]), float(r[2]), float(r[3]), float(r[4])
        if min(o, h, l, c) <= 0:
            nonpos += 1
        if h < max(o, c) or l > min(o, c) or h < l:
            bad += 1
        if float(r[5]) == 0:
            zero_v += 1
    return bad, nonpos, zero_v


def do_symbol(inst, ctx):
    out, http, start, end_eff, force = ctx["out"], ctx["http"], ctx["start"], ctx["end_eff"], ctx["force"]
    sym = inst["symbol"]
    fn = X.safe_name(sym)
    if not force and X.is_done(out, EX, fn):
        return "пропуск (.done)"
    a, b = candle_bounds(inst, start, end_eff)
    if a >= b:
        X.mark_done(out, EX, fn, "rows=0 (листинг после окна: launch=%s)" % inst.get("listed_ms"))
        ln = inst.get("listed_ms")
        return "нет данных в окне (листинг %s)" % (X.ms_to_str(int(ln)) if ln not in ("", None) else "?")
    try:
        rows, rq_c = fetch_candles(http, sym, a, b)
    except Paused as e:
        return "ПАУЗА (%s) — не помечен .done" % e
    clean = {}
    for r in rows:
        if start <= r[0] < end_eff:
            clean[r[0]] = r
    rows = [clean[t] for t in sorted(clean)]
    nbad, nnonpos, nzero = ohlc_check(rows)
    n, gaps = (0, 0)
    if rows:
        n, gaps = X.write_candles(os.path.join(out, EX, "candles", fn + ".csv.gz"), rows, start_ms=start, end_ms=end_eff)
    frows, rq_f = fetch_funding(http, sym, start, end_eff, inst.get("funding_interval_h"))
    nf = X.write_funding(os.path.join(out, EX, "funding", fn + ".csv.gz"), frows) if frows else 0
    info = "rows=%d gaps=%d bad_ohlc=%d nonpos=%d zero_vol=%d funding=%d reqs=%d+%d" % (n, gaps, nbad, nnonpos, nzero, nf, rq_c, rq_f)
    X.mark_done(out, EX, fn, info)
    return info


def plan(insts, start, end_eff, rate):
    n_req, n_with = 0, 0
    for i in insts:
        a, b = candle_bounds(i, start, end_eff)
        if a >= b:
            continue
        n_with += 1
        n_req += -(-(b - a) // (KL_LIMIT * X.MIN_MS)) + 1
    return n_with, n_req, n_req / max(rate, 0.01)


def main():
    ap = X.common_args("BingX: минутные свечи и фандинг всех перпетуалов (крипта + TradFi) за окно", default_rate=5.0, default_workers=4)
    a = ap.parse_args()
    start, end = X.parse_ts(a.start), X.parse_ts(a.end)
    now_min = ((int(time.time() * 1000) - 3000) // X.MIN_MS) * X.MIN_MS
    end_eff = min(end, now_min)                              # незакрытую текущую минуту не берём
    X.ensure_dirs(a.out, EX)
    log = X.make_logger(a.out, EX, "bingx")
    http = BxHttp(rate=a.rate, retries=8, timeout=30)
    log("окно [%s, %s) UTC; эффективный конец %s; rate=%s workers=%d" % (X.ms_to_str(start), X.ms_to_str(end), X.ms_to_str(end_eff), a.rate, a.workers))
    insts = fetch_instruments(http, log)
    p = X.write_instruments(a.out, EX, insts)
    by = {}
    for i in insts:
        by[i["class"]] = by.get(i["class"], 0) + 1
    log("контрактов %d -> %s; по классам: %s" % (len(insts), p, json.dumps(by, ensure_ascii=False, sort_keys=True)))
    sel = insts
    if a.symbols:
        want = [s.strip() for s in a.symbols.split(",") if s.strip()]
        known = {i["symbol"] for i in insts}
        miss = [s for s in want if s not in known]
        if miss:
            log("нет в списке биржи: %s" % ",".join(miss))
        sel = [i for i in insts if i["symbol"] in set(want)]
    if a.limit_symbols:
        sel = sel[:a.limit_symbols]
    n_with, n_req, est = plan(sel, start, end_eff, a.rate)
    log("план: контрактов с данными в окне %d из %d, запросов ≈ %d (свечи+фандинг), при rate=%s ≈ %.0f мин" % (n_with, len(sel), n_req, a.rate, est / 60))
    if a.list_only:
        return 0
    ctx = {"out": a.out, "http": http, "start": start, "end_eff": end_eff, "force": a.force}
    res = X.run_parallel(sel, lambda it: do_symbol(it, ctx), a.workers, log)
    log("HTTP: %s" % json.dumps(http.stats, ensure_ascii=False, sort_keys=True, default=str))
    for s, e in res["fails"]:
        log("СБОЙ-ИТОГ %s: %s" % (s, e))
    return 1 if res["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
