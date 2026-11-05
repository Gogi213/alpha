#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bitget USDT-M и USDC-M перпетуалы: свечи 1 м + история фандинга (публичные данные, без ключей).

Формат результата и общие функции — xdl_common.py (рядом). Только стандартная библиотека, python 3.11+.

Что найдено по реальному API (05.11.2026, проверено с VPS София):
  * /api/v2/mix/market/contracts?productType=usdt-futures (884 контракта) и usdc-futures (49): все symbolType=perpetual.
    Поле isRwa=YES помечает TradFi-перпы (акции, ETF, металлы, нефть/газ, индексы, FX и т. п. — 420 из 884 USDT-контрактов);
    подкласса у биржи нет, поэтому подкласс — по тикеру (словари ниже), class_src это фиксирует. Список отдаёт только
    живые контракты (все symbolStatus=normal, offTime=-1): снятые с листинга внутри окна этим эндпоинтом не перечислить.
  * history-candles: limit ≤ 200 (201+ -> HTTP 400 code 40053). С startTime+endTime отдаёт ПОСЛЕДНИЕ limit свечей окна
    [startTime, endTime) по возрастанию -> пагинация только назад (endTime = самая ранняя свеча страницы). endTime
    исключительный (свеча с t_open >= endTime не приходит, т. е. незакрытая свеча не попадает). Глубина — годы.
    Минуты без сделок биржа ЗАПОЛНЯЕТ плоскими свечами с v=0 (дыр в ответе нет).
  * candles (быстрый): limit ≤ 1000, окно (startTime, endTime] (старт исключительный, конец ВКЛЮЧИТЕЛЬНЫЙ), глубина ровно
    1 календарный месяц от «сейчас» (31 сут назад — да, 31,04 сут — пусто). Значения идентичны history-candles (сверено
    на ETH/AAPL/XAU по 400 минутам). Отсюда режим hybrid: старую часть окна берём history-candles, свежую — candles
    (≈ в 3 раза меньше запросов); --source history отключает hybrid.
  * Строка свечи: [t_open_ms, o, h, l, c, baseVolume, quoteVolume] — объём уже в базовой монете (контракты Bitget
    USDT/USDC-M считаются в монетах; v*close ~ quote_v), перевода через размер контракта не нужно (contract_size=1).
  * history-fund-rate: pageSize ≤ 100 (больше — молча 100; без pageSize — 20), страницы новые->старые, глубина месяцы;
    строка {symbol, fundingRate, fundingTime}; интервала в строке нет — берётся из разности соседних выплат (запас —
    fundInterval контракта: 8/4/1 ч). Интервал у Bitget ДИНАМИЧЕСКИЙ: при упоре ставки в предел контракт переходит
    8 ч -> 4 ч -> 1 ч (HOTUSDT 03.11: 8,8,...,3(переход),1,1,...), поэтому interval_h в файле = часов с предыдущей
    выплаты (на переходе бывает нестандартное 2/3/5/6/7), а не константа контракта.
  * Лимиты: документировано 20 запр/с на IP на эндпоинт; заголовок x-mbx-used-remain-limit показывает остаток: у
    contracts/candles/fund-rate он 19-20, у history-candles ≤ 4-5 (т. е. у history-candles, вероятно, ~5 запр/с) —
    поэтому у history-candles отдельный ограничитель --rate-history (по умолчанию 4).
"""
import os, sys, time, re, calendar, datetime, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xdl_common as X

EX = "bitget"
API = "https://api.bitget.com"
EP_CONTRACTS = API + "/api/v2/mix/market/contracts"
EP_HIST = API + "/api/v2/mix/market/history-candles"
EP_CAND = API + "/api/v2/mix/market/candles"
EP_FUND = API + "/api/v2/mix/market/history-fund-rate"
HIST_LIMIT, CAND_LIMIT, FUND_PAGE = 200, 1000, 100
HOUR_MS = 3_600_000
DAY_MS = 86_400_000

# ---------------------------------------------------------------- классы активов (isRwa=YES -> по тикеру) ----------
def _set(s):
    return set(s.split())

METAL = _set("XAU XAG XPT XPD COPPER PAXG XAUT")
ENERGY = _set("CL BZ NATGAS")
FX = _set("EURUSD USDJPY GBPUSD USDBRL")
INDEX = _set("NDX100 SP500 HSI JP225 KR200")
ETF = _set("""QQQ SPY IWM VOO TQQQ SQQQ SOXL SOXS SOXX SMH XLU XLK XLV XLE EWJ EWY EWT EWH EWZ EWZS EIDO INDA KWEB GDX URNM
UVXY TZA TMF TBT TLT SGOV BITO IBIT IBB XBI BOTZ JEPQ QLD DIASTOCK BWET KSTR
MUU MSTU MSFU AAPU AMZU METU GGLL NVDL TSLL CONL KORU DFEN CSOPSK2LHKD CSOPSS2LHKD""")
LEVERAGED = _set("TQQQ SQQQ SOXL SOXS TZA TMF TBT UVXY QLD MUU MSTU MSFU AAPU AMZU METU GGLL NVDL TSLL CONL KORU DFEN "
                 "CSOPSK2LHKD CSOPSS2LHKD")
# частные/доIPO компании: рынка акций нет, класс other
PRE_IPO = _set("OPENAI ANTHROPIC MOONSHOT OURA POLYMARKET")
# тикеры, подкласс которых я не смог установить уверенно (часть, судя по названию, плечевые ETF) -> other + пояснение
UNKNOWN = {
    "H100": "индекс цены аренды GPU (не акция и не товарный фьючерс) - уточнить",
    "B200": "индекс цены аренды GPU (не акция и не товарный фьючерс) - уточнить",
    "SPCX": "SPAC-ETF либо прокси SpaceX - не установлено",
    "DRAM": "вероятно ETF по памяти - не установлено",
    "USDESTOCK": "тикер USDE с суффиксом STOCK - не установлено",
}
for _t in "XNDU ECHO DISK EUV JMKE SPCH SSPC SKHY SKDD SKUU MVLL RAM BSP BNC INTW SNXX BOT SHAZ ZCSH AGPU SECZ ELICE".split():
    UNKNOWN[_t] = "тикер TradFi-перпа, не опознан (возможно плечевой ETF/акция) - уточнить"
# страна листинга (для заметки: вне часов своей биржи свечи плоские, v=0)
_REG = {}
for _r, _s in (
    ("JP", "KIOXIA TOKYOEL SUMIELEC ADVANTEST LASERTEC TAIYOYUDEN MURATA JPMUFG TOYOTA JPSONY FASTRETAILING SOFTBANK JP225"),
    ("KR", "SKHYNIX SAMSUNG SAMSUNGEM HYUNDAI NAVER LGELECTRONICS HANMI KAKAO KBFG KIA LGENERGY SSBIOLOGICS DOOSENER DOOSBOT KR200 CSOPSK2LHKD CSOPSS2LHKD"),
    ("TW", "TWTSM TWDELTA TWASX TWUMC NANYA WINBOND YAGEO WALSIN HONHAI MEDIATEK"),
    ("IN", "JIO HINDUNILVR ITC LT BHARTIARTL TCS INFY SBIN ICICIBANK HDFCBANK RELIANCE"),
    ("HK/CN", "TENCENT XIAOMI MEITUAN KUAISHOU POPMART MINIMAX ZHIPU BYD ZHONGJI GIGADEVICE SMIC NETEASE LENOVOHKD MINIMAXHKD "
              "XIAOMIHKD TENCENTHKD ZHIPUHKD SHEIN SHEINHKD CXMT UNITREE HSI"),
):
    for _t in _s.split():
        _REG[_t] = _r
# явный список акций (= все текущие isRwa=YES-тикеры минус словари выше). Новый TradFi-тикер вне словарей -> other.
EQUITY = _set("""
TSLA CRCL NVDA GOOGL AAPL AMZN META MCD MSTR COIN HOOD ARM ASML BABA INTC IBM APP GE ORCL PLTR GME MRVL MSFT NFLX FUTU JD RDDT
CSCO PEP ACNSTOCK MA UNH LLY AVGO MU COST WMT TSM COP XOM OXY RKLB BA FLY SNDK LITE AMD AMAT STXSTOCK AAOI COHR BE NBIS IONQ
OKLO MP CRDO CRWV APLD KLAC AXTI LWLG KOPN USAR NIO CBRS INFQ VRT ETN NOKSTOCK OUST POET ONDS RDW QCOM WDC SMCI ASTS JOBY ACHR
RGTI QBTS QUBT ARQQ BRKB PANW SNOW DELL QNTSTOCK EVEX KTOS AVAV AXON LMT RTXSTOCK NOC NOW NTAP SKHYNIX SAMSUNG HYUNDAI SITM SMR
BBSTOCK KIOXIA TOKYOEL SUMIELEC ADVANTEST LASERTEC DOOSENER DOOSBOT PL CIEN AMC MDB TWLO DXYZ ADBE IREN SPIR SATL BX HPE CRWD
SIMO GLW TSEM OSS AEHR V ABNB CMCSA KO FOXA MAR NKE BUD CCL DKNG BMNR ISRG ROK CGNX FLNC LRCX ALAB TER FLEX APD LIN ADI GS CAT
JPM GEV STRC MINIMAX ZHIPU SONY WEN AAL TXN BAC CRM AMKR PDD ASX TENCENT GIGADEVICE SMIC POPMART XIAOMI MEITUAN NETEASE TTWO
FWDI KUAISHOU TM MUFG CPNG MINIMAXHKD XIAOMIHKD TENCENTHKD ZHIPUHKD SOFI PENG GILD BKNG ZM AMGN PYPL SHOP VST DDOG UBER MELI
ANET NAVER LGELECTRONICS HANMI SAMSUNGEM ZHONGJI CXMT LYTE NET UNITREE MRNA TEM MRK DE DJT MARA PURR HPQ SHEIN OKTA VEEV SHEINHKD
NVS GTLB ZS TEAM GPRO BYD SOFTBANK FCX BHP RIO VALE CVXSTOCK CLSK SDGR GNRC PATH CYPH HUT CORZ BLSH TWST CVNA CRML KOD RUM AKAM
VKTX PCVX XP CEG SBUX LEN HD KAKAO KBFG SSBIOLOGICS LGENERGY KIA SNPS MURATA JPMUFG TOYOTA TAIYOYUDEN JPSONY FASTRETAILING JIO
TWTSM TWDELTA TWASX TWUMC NANYA WINBOND YAGEO WALSIN MDT CSTOCK BP VLO EBAY IBKR HLT MSCI ABBV HONHAI MEDIATEK DHR TSTOCK SPOT
PG CSX STM EQT ITUB DASHSTOCK NU PBR CLS FTNT PFE HINDUNILVR LT ITC BHARTIARTL TCS INFY SBIN ICICIBANK HDFCBANK RELIANCE MO LNG
FISV MS KMB LENOVOHKD GFS
""")


def classify(base, is_rwa):
    """-> (class, class_src, note). Биржа даёт только isRwa; подкласс — по словарям тикеров."""
    if not is_rwa:
        note = "стейбл-перп USDC/USDT" if base == "USDC" else ""
        return "crypto", "isRwa=NO", note
    src = "isRwa=YES+ticker_map"
    reg = _REG.get(base)
    rnote = ("листинг %s; вне часов базового рынка свечи плоские" % reg) if reg else ""
    if base in METAL:
        n = "токенизированное физическое золото (крипто-токен с привязкой к золоту)" if base in ("PAXG", "XAUT") else ""
        return "metal", src, n
    if base in ENERGY:
        return "energy", src, ""
    if base in FX:
        return "fx", src, ""
    if base in INDEX:
        return "index", src, rnote
    if base in ETF:
        n = "плечевой/инверсный ETP" if base in LEVERAGED else ""
        return "etf", src, "; ".join(x for x in (n, rnote) if x)
    if base in PRE_IPO:
        return "other", src, "частная/до-IPO компания (синтетический перп) - не акция"
    if base in UNKNOWN:
        return "other", "isRwa=YES (подкласс не установлен)", UNKNOWN[base]
    if base in EQUITY:
        n = {"DXYZ": "закрытый фонд (closed-end fund)", "STRC": "привилегированная бумага Strategy"}.get(base, "")
        return "equity", src, "; ".join(x for x in (n, rnote) if x)
    return "other", "isRwa=YES (тикер вне словаря)", "новый TradFi-тикер, подкласс не определён - добавить в bitget.py"


# ---------------------------------------------------------------- обёртка API ----------------------------------------
class Api:
    """Вызов с проверкой кода Bitget, мягким замедлением при 429 и понятными ошибками."""
    def __init__(self, http, log, name):
        self.http, self.log, self.name = http, log, name
        self.slowdowns = 0

    def _slow(self):
        old = self.http.rl.dt
        self.http.rl.dt = min(old * 1.25, 1.0)
        self.slowdowns += 1
        self.log("429/лимит на %s: интервал %.3f -> %.3f с" % (self.name, old, self.http.rl.dt))

    def get(self, url, params):
        tail = url.rsplit("/", 1)[-1]
        for attempt in range(6):
            n429 = self.http.stats["http_err"].get(429, 0)
            try:
                j = self.http.get_json(url, params)
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", "replace")[:200]
                except Exception:
                    pass
                raise RuntimeError("HTTP %d %s %s | %s" % (e.code, tail, params.get("symbol", ""), body))
            if self.http.stats["http_err"].get(429, 0) > n429:
                self._slow()
            code = j.get("code") if isinstance(j, dict) else None
            if code == "00000":
                return j.get("data")
            msg = str(j.get("msg", "")) if isinstance(j, dict) else str(j)[:100]
            if code in ("429", "40014") or "too many" in msg.lower():
                self._slow()
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError("bitget code=%s msg=%s | %s %s" % (code, msg, tail, params.get("symbol", "")))
        raise RuntimeError("лимит запросов не снят: %s %s" % (tail, params.get("symbol", "")))


def _row(r):
    return (int(r[0]), r[1], r[2], r[3], r[4], r[5], r[6] if len(r) > 6 else None)


def walk_history(api, sym, pt, s, e):
    """[s, e) через history-candles: страницы по 200 назад по endTime. -> (rows, число запросов)."""
    rows, n, end = [], 0, e
    while end > s:
        d = api.get(EP_HIST, dict(symbol=sym, productType=pt, granularity="1m", startTime=s, endTime=end, limit=HIST_LIMIT))
        n += 1
        if not d:
            break
        rows.extend(_row(r) for r in d)
        oldest = min(int(r[0]) for r in d)
        if oldest >= end:        # нет продвижения — защита от зацикливания
            break
        end = oldest
    return rows, n


def walk_candles(api, sym, pt, s, e):
    """[s, e) через candles (старт исключительный, конец включительный): страницы по 1000 назад. -> (rows, запросов)."""
    rows, n, end_incl = [], 0, e - X.MIN_MS
    while end_incl >= s:
        d = api.get(EP_CAND, dict(symbol=sym, productType=pt, granularity="1m", startTime=s - 1, endTime=end_incl, limit=CAND_LIMIT))
        n += 1
        if not d:
            break
        rows.extend(_row(r) for r in d)
        oldest = min(int(r[0]) for r in d)
        if oldest > end_incl:
            break
        if oldest <= s:
            break
        end_incl = oldest - X.MIN_MS
    return rows, n


def fetch_funding(api, sym, pt, s, e, default_h):
    """-> (строки (time_ms, rate, interval_h) окна [s, e), число запросов)."""
    allr, page, n = {}, 1, 0
    while True:
        d = api.get(EP_FUND, dict(symbol=sym, productType=pt, pageSize=FUND_PAGE, pageNo=page))
        n += 1
        if not d:
            break
        for x in d:
            allr[int(x["fundingTime"])] = x["fundingRate"]
        oldest = min(int(x["fundingTime"]) for x in d)
        if len(d) < FUND_PAGE or oldest < s or page >= 80:
            break
        page += 1
    ts = sorted(allr)
    out = []
    for i, t in enumerate(ts):
        if not (s <= t < e):
            continue
        iv = default_h
        if i > 0:
            dh = (t - ts[i - 1]) / HOUR_MS
            if abs(dh - round(dh)) < 0.02 and 1 <= round(dh) <= 24:
                iv = int(round(dh))
        out.append((t, allr[t], iv if iv != "" else None))
    return out, n


# ---------------------------------------------------------------- список контрактов ----------------------------------
def _num(x, mul=1.0):
    try:
        return "%g" % (float(x) * mul)
    except (TypeError, ValueError):
        return ""


def fetch_instruments(api, product_types, log):
    out, seen = [], set()
    for pt in product_types:
        d = api.get(EP_CONTRACTS, dict(productType=pt))
        if not d:
            log("productType %s: контрактов нет" % pt)
            continue
        skipped = 0
        for x in d:
            if x.get("symbolType") != "perpetual":
                skipped += 1
                continue
            sym = x["symbol"]
            if sym in seen:
                continue
            seen.add(sym)
            base, quote = x["baseCoin"], x["quoteCoin"]
            cls, src, note = classify(base, x.get("isRwa") == "YES")
            m = re.match(r"^(1000000|1000|1M)(?=[^0-9])", base)
            parts = ["pt=%s" % pt, "lot=%s" % x.get("minTradeNum", ""), "maxLev=%s" % x.get("maxLever", "")]
            if m:
                parts.append("множитель %s: цена и объём за %s монет (префикс биржи)" % (m.group(1), m.group(1)))
            if note:
                parts.append(note)
            try:
                tick = float(x["priceEndStep"]) / (10 ** int(x["pricePlace"]))
                tick = "%.10g" % tick
            except (KeyError, ValueError, TypeError):
                tick = ""
            off = x.get("offTime", "")
            out.append(dict(
                exchange=EX, symbol=sym, native_id=sym, base=base, quote=quote,
                settle=(x.get("supportMarginCoins") or [quote])[0], **{"class": cls}, class_src=src,
                status=x.get("symbolStatus", ""), contract_size="1", tick_size=tick,
                listed_ms=x.get("openTime") or x.get("launchTime") or "",
                delisted_ms=("" if off in ("", "-1", "0") else off),
                funding_interval_h=x.get("fundInterval", ""),
                taker_fee_bps=_num(x.get("takerFeeRate"), 1e4), maker_fee_bps=_num(x.get("makerFeeRate"), 1e4),
                note="; ".join(parts), _pt=pt))
        log("productType %s: %d контрактов%s" % (pt, len(d), (" (не perpetual пропущено %d)" % skipped) if skipped else ""))
    return out


def month_back(now_ms):
    d = datetime.datetime.fromtimestamp(now_ms / 1000, datetime.timezone.utc)
    y, m = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
    day = min(d.day, calendar.monthrange(y, m)[1])
    return int(d.replace(year=y, month=m, day=day).timestamp() * 1000)


# ---------------------------------------------------------------- один контракт --------------------------------------
class Ctx:
    pass


def run_symbol(c, inst):
    sym, pt = inst["symbol"], inst["_pt"]
    name = X.safe_name(sym)
    if not c.force and X.is_done(c.out, EX, name):
        return "пропуск (.done)"
    s, e = c.start, c.eff_end
    listed = int(inst["listed_ms"]) if str(inst.get("listed_ms", "")).isdigit() else None
    rows, nreq, note = [], 0, ""
    if listed is not None and listed >= e:
        note = " listed_after_window=%s (no candles/funding requested)" % X.ms_to_str(listed)
    elif s < e:
        if c.source == "history":
            segs = [("h", s, e)]
        else:
            cut = min(max(c.cutoff, s), e)
            segs = ([("h", s, cut)] if s < cut else []) + ([("c", cut, e)] if cut < e else [])
        for kind, a, b in segs:
            if kind == "h":
                r, n = walk_history(c.api_h, sym, pt, a, b)
            else:
                r, n = walk_candles(c.api_m, sym, pt, a, b)
                if not r:     # быстрый эндпоинт пуст -> подстраховка: проверить тем же окном через history-candles
                    r, n2 = walk_history(c.api_h, sym, pt, a, b)
                    n += n2
                    if r:
                        note += " fallback_history=%d" % len(r)
            rows.extend(r)
            nreq += n
    path = os.path.join(c.out, EX, "candles", name + ".csv.gz")
    nrows, gaps = X.write_candles(path, rows, start_ms=s, end_ms=e)
    # проверки значений (данные не меняем, только считаем)
    bad = zerov = 0
    for r in {x[0]: x for x in rows if s <= x[0] < e}.values():
        try:
            o, h, l, cl = float(r[1]), float(r[2]), float(r[3]), float(r[4])
            if min(o, h, l, cl) <= 0 or h < max(o, cl) or l > min(o, cl):
                bad += 1
            if r[5] is not None and float(r[5]) == 0:
                zerov += 1
        except ValueError:
            bad += 1
    dfl = int(inst["funding_interval_h"]) if str(inst.get("funding_interval_h", "")).isdigit() else ""
    if listed is not None and listed >= e:
        frows, nf = [], 0
    else:
        frows, nf = fetch_funding(c.api_m, sym, pt, s, e, dfl)
    X.write_funding(os.path.join(c.out, EX, "funding", name + ".csv.gz"), frows)
    info = "rows=%d gaps=%d bad_ohlc=%d zero_v=%d fund=%d req=%d%s" % (nrows, gaps, bad, zerov, len(frows), nreq + nf, note)
    X.mark_done(c.out, EX, name, info)
    with c.lock:
        c.tot["rows"] += nrows
        c.tot["gaps"] += gaps
        c.tot["bad"] += bad
        c.tot["req"] += nreq + nf
    return info


def main():
    import threading
    ap = X.common_args("Bitget USDT-M/USDC-M: свечи 1 м + фандинг (публичные данные)", default_rate=12.0, default_workers=6)
    ap.add_argument("--rate-history", type=float, default=4.0, help="запросов/с к history-candles (у него отдельный, меньший, бюджет)")
    ap.add_argument("--source", choices=("hybrid", "history"), default="hybrid",
                    help="hybrid: свежий месяц через candles (limit 1000), старое — history-candles (limit 200); history: только history-candles")
    ap.add_argument("--candles-depth-days", type=float, default=0,
                    help="глубина эндпоинта candles в сутках; 0 = авто (календарный месяц назад + 1 сутки запаса)")
    ap.add_argument("--product-types", default="usdt-futures,usdc-futures")
    a = ap.parse_args()
    X.ensure_dirs(a.out, EX)
    log = X.make_logger(a.out, EX, "list" if a.list_only else "run")
    start, end = X.parse_ts(a.start), X.parse_ts(a.end)
    now = int(time.time() * 1000) // X.MIN_MS * X.MIN_MS
    eff_end = min(end, now)           # незакрытую текущую минуту не берём
    if a.candles_depth_days > 0:
        cutoff = now - int(a.candles_depth_days * DAY_MS)
    else:
        cutoff = month_back(now) + DAY_MS
    http_m, http_h = X.Http(rate=a.rate), X.Http(rate=a.rate_history)
    api_m, api_h = Api(http_m, log, "candles/funding/contracts"), Api(http_h, log, "history-candles")
    log("окно [%s, %s) eff_end=%s source=%s граница history|candles=%s rate=%.1f rate-history=%.1f workers=%d" % (
        X.ms_to_str(start), X.ms_to_str(end), X.ms_to_str(eff_end), a.source, X.ms_to_str(cutoff), a.rate, a.rate_history, a.workers))
    pts = [p.strip() for p in a.product_types.split(",") if p.strip()]
    insts = fetch_instruments(api_m, pts, log)
    X.write_instruments(a.out, EX, insts)
    by = {}
    for i in insts:
        by[i["class"]] = by.get(i["class"], 0) + 1
    log("контрактов %d; по классам: %s" % (len(insts), ", ".join("%s=%d" % kv for kv in sorted(by.items(), key=lambda kv: -kv[1]))))
    try:
        cf = api_m.get(EP_CONTRACTS, dict(productType="coin-futures"))
        if cf:
            log("ВНИМАНИЕ: coin-futures непусто (%d) - не скачиваются (семантика объёма не проверена)" % len(cf))
    except Exception as ex:
        log("coin-futures: %s" % str(ex)[:120])
    if a.list_only:
        return 0
    items = insts
    if a.symbols:
        want = {x.strip() for x in a.symbols.split(",") if x.strip()}
        items = [i for i in items if i["symbol"] in want]
        miss = want - {i["symbol"] for i in items}
        if miss:
            log("нет в списке: %s" % ",".join(sorted(miss)))
    if a.limit_symbols:
        items = items[:a.limit_symbols]
    c = Ctx()
    c.out, c.start, c.eff_end, c.cutoff, c.source, c.force = a.out, start, eff_end, cutoff, a.source, a.force
    c.api_m, c.api_h, c.lock = api_m, api_h, threading.Lock()
    c.tot = {"rows": 0, "gaps": 0, "bad": 0, "req": 0}
    res = X.run_parallel(items, lambda it: run_symbol(c, it), a.workers, log)
    log("свечей строк=%d дыр=%d битых OHLC=%d запросов(контракты)=%d | http main: %s | http history: %s | замедлений 429: %d/%d" % (
        c.tot["rows"], c.tot["gaps"], c.tot["bad"], c.tot["req"], http_m.stats, http_h.stats, api_m.slowdowns, api_h.slowdowns))
    if res["fails"]:
        log("СБОИ (%d): %s" % (len(res["fails"]), res["fails"][:20]))
    return 1 if res["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
