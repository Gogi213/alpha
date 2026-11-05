#!/usr/bin/env python3
"""Binance USD-M и Aster (Binance-совместимое API): минутные свечи + история фандинга ВСЕХ перпетуалов за окно (по умолчанию
[2026-10-01, 2026-11-05) UTC). Публичные данные, ключей нет. Формат результата — xdl_common (см. его докстринг).

Источники свечей:
  rest     — /fapi/v1/klines (limit по умолчанию 499 = вес 2 на обеих биржах; 1000 = вес 5; 1500 = вес 10);
  auto     — (только binance, по умолчанию) архив data.binance.vision (месячный zip на целые месяцы окна, дневные zip на остальные
             сутки) + REST только на сутки, которых в архиве нет (в архиве бывают целые пропавшие сутки: 2026-10-28 нет ни у одного
             символа; REST их отдаёт). Внутрисуточных проверок нет: файл суток либо есть целиком, либо его нет;
  archive  — только архив, без REST-дозаливки (для замеров).
У Aster архива нет — всегда REST.

Ограничения бирж (IP): вес 2400/мин (заголовок X-MBX-USED-WEIGHT-1M; governor держит ниже --weight-cap), для /fapi/v1/fundingRate и
/fapi/v1/fundingInfo — отдельный лимит 500 запросов/5 мин на IP (--fund-rate 1.4/с). 429/418 — пауза по Retry-After для всех потоков.

Фандинг: запрашиваем с запасом -24 ч, чтобы интервал строки считать как разность до предыдущей выплаты; в файл идут выплаты с
номинальным временем (время, округлённое до секунды) внутри окна; time_ms — как отдала биржа (у выплат есть дрожание в несколько мс).
"""
import sys, os, io, re, json, time, gzip, zipfile, threading, datetime, random, ssl, http.client, urllib.parse, collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xdl_common import (common_args, parse_ts, ensure_dirs, make_logger, write_candles, write_funding, write_instruments, is_done,
                        mark_done, safe_name, run_parallel, RateLimiter, MIN_MS)

DAY_MS = 86_400_000
FAPI = {"binance": "https://fapi.binance.com", "aster": "https://fapi.asterdex.com"}
ARCH = "https://data.binance.vision/data/futures/um"
BIG_DELIVERY = 4_000_000_000_000   # заглушка deliveryDate у живых перпетуалов (4133404800000)


# ----------------------------------------------------------------------------------------------------------------- классы
METAL = {"XAU", "XAG", "XPT", "XPD", "XCU", "COPPER", "PAXG", "XAUT"}
ENERGY = {"CL", "BZ", "NATGAS", "NG", "WTI", "BRENT", "USOIL", "UKOIL"}
GOLD_TOKENS = {"PAXG", "XAUT"}
STABLE = {"USDC", "USDT", "USDE", "DAI", "FDUSD", "TUSD", "USD1", "PYUSD", "BUSD", "U"}
# Известные ETF/ETN-тикеры (биржи сами помечают ETF только частично: Binance — не помечает совсем, Aster — не всё). Консервативный список.
ETF_TICKERS = {"SPY", "QQQ", "IWM", "DIA", "EWY", "EWT", "EWJ", "EWZ", "EWZS", "EIDO", "SOXL", "SOXS", "SQQQ", "TQQQ", "UVXY", "XLE",
               "XLF", "XLK", "XBI", "SMH", "GDX", "TBT", "TMF", "TZA", "TSLL", "NVDL", "BITO", "URNM", "KORU", "DRAM", "KSTR", "LYTE",
               "MSTU", "KODEX200", "BWET", "CSOPSKHYNIX2L", "CSOPSAMSUNG2L"}


def multiplier_note(base):
    m = re.match(r"^(\d+)(?=[A-Z])", base)
    if m and int(m.group(1)) >= 10 and set(m.group(1)) <= {"0", "1"}:
        return "множитель %s: цена и объём за %s ед. базового актива" % (m.group(1), m.group(1))
    if re.match(r"^1M[A-Z]", base):
        return "множитель 1000000 (1M): цена и объём за 1 000 000 ед. базового актива"
    return ""


def classify_binance(s):
    """-> (class, class_src, note-список). Метаданные: contractType / underlyingType / underlyingSubType."""
    ut, sub, base = s.get("underlyingType", ""), s.get("underlyingSubType", []) or [], s.get("baseAsset", "")
    ct = s.get("contractType", "")
    notes = []
    src = "exchange:underlyingType=%s|sub=%s" % (ut, "+".join(sub))
    if base in GOLD_TOKENS:
        return "metal", "ticker:gold-token (биржа: %s)" % ut, ["токен на золото (крипто-актив, цена ≈ золото)"]
    if ut == "COIN":
        return "crypto", src, notes
    if ut in ("EQUITY", "HK_EQUITY", "KR_EQUITY", "CN_EQUITY"):
        if ut != "EQUITY":
            notes.append("регион " + ut.split("_")[0])
        if base in ETF_TICKERS:
            return "etf", "exchange:%s+ticker-list:etf" % ut, notes + ["Binance ETF не помечает; класс по списку тикеров"]
        return "equity", src, notes
    if ut == "COMMODITY":
        if base in METAL:
            return "metal", src, notes
        if base in ENERGY:
            return "energy", src, notes
        return "commodity", src, notes
    if ut == "FX":
        if base in STABLE:
            return "crypto", src, ["стейбл-коин-пара, не фиат"]
        return "fx", src, notes
    if ut == "INDEX":
        if "TradFi" in sub:
            return "index", src, notes
        return "crypto", src, ["крипто-индекс (не TradFi)"]
    if ut == "PREMARKET":
        if "Pre-IPO" in sub or "TradFi" in sub:
            return "other", src, ["pre-IPO частная компания (не акция в обращении): неоднозначно"]
        return "crypto", src, ["pre-market токен до листинга"]
    return "other", src, ["неизвестный underlyingType=%r contractType=%s" % (ut, ct)]


def classify_aster(s):
    """Aster: underlyingType всегда COIN; TradFi помечен в underlyingSubType (STOCK / ETF / Commodities / pre-launch / AOS2)."""
    sub, base, name = s.get("underlyingSubType", []) or [], s.get("baseAsset", ""), (s.get("name") or "")
    subs = set(sub)
    src = "exchange:underlyingSubType=%s" % "+".join(sub)
    notes = []
    if base in GOLD_TOKENS:
        return "metal", src + "+ticker:gold-token", ["токен на золото (крипто-актив, цена ≈ золото)"]
    if "pre-launch" in subs:
        if "STOCK" in subs:
            return "other", src, ["pre-launch / pre-IPO частная компания (не акция в обращении): неоднозначно"]
        return "crypto", src, ["pre-launch токен до листинга"]
    if "Commodities" in subs:
        low = name.lower()
        if base in METAL or any(k in low for k in ("gold", "silver", "copper", "platinum", "palladium")):
            return "metal", src + ("+name=" + name if name else ""), notes
        if base in ENERGY or any(k in low for k in ("oil", "gas", "crude", "brent")):
            return "energy", src + ("+name=" + name if name else ""), notes
        return "commodity", src, notes
    if "ETF" in subs:
        return "etf", src, notes
    if "STOCK" in subs:
        if base in ETF_TICKERS:
            return "etf", src + "+ticker-list:etf", ["биржа пометила STOCK; класс ETF по списку тикеров"]
        return "equity", src, notes
    if "AOS2" in subs:
        return "crypto", src, ["AOS2-контракт (котировка %s)" % s.get("quoteAsset")]
    return "crypto", src, notes


def build_instrument(ex, s, fund_h):
    cls, csrc, notes = (classify_binance if ex == "binance" else classify_aster)(s)
    mn = multiplier_note(s.get("baseAsset", ""))
    if mn:
        notes.append(mn)
    if ex == "binance" and s.get("contractType") == "TRADIFI_PERPETUAL":
        notes.append("TRADIFI_PERPETUAL")
    if s.get("quoteAsset") not in STABLE:
        notes.append("котировка %s: цена не в долларах" % s.get("quoteAsset"))
    if s.get("baseAsset") in ("BTCDOM", "DEFI", "ALL") and cls == "crypto" and not any("индекс" in n for n in notes):
        notes.append("крипто-индекс (не TradFi)")
    tick = ""
    for f in s.get("filters", []):
        if f.get("filterType") == "PRICE_FILTER":
            tick = f.get("tickSize", "")
    dv = int(s.get("deliveryDate") or 0)
    status = s.get("status", "")
    return {"exchange": ex, "symbol": s["symbol"], "native_id": s["symbol"], "base": s.get("baseAsset", ""), "quote": s.get("quoteAsset", ""),
            "settle": s.get("marginAsset", s.get("quoteAsset", "")), "class": cls, "class_src": csrc, "status": status,
            "contract_size": 1, "tick_size": tick, "listed_ms": int(s.get("onboardDate") or 0),
            "delisted_ms": (dv if (dv and dv < BIG_DELIVERY and status != "TRADING") else ""),
            "funding_interval_h": fund_h.get(s["symbol"], ""), "taker_fee_bps": "", "maker_fee_bps": "", "note": "; ".join(notes)}


# ----------------------------------------------------------------------------------------------------------------- сеть
class HttpErr(Exception):
    def __init__(self, code, body, url):
        super().__init__("HTTP %s %s %s" % (code, url[:140], body[:200]))
        self.code, self.body = code, body


class Net:
    """Keep-alive клиент (соединение на поток и хост), ограничители на хост, governor веса, глобальная пауза при 429/418."""
    def __init__(self, weight_cap, log, retries=6, timeout=40, ua="alpha-xdl/1"):
        self.tl = threading.local()
        self.lock = threading.Lock()
        self.retries, self.timeout, self.ua, self.log = retries, timeout, ua, log
        self.weight_cap = weight_cap
        self.used_w, self.used_t, self.max_used = 0, 0.0, 0
        self.ban_until = 0.0
        self.stats = {"req": 0, "retry": 0, "bytes": 0, "codes": collections.Counter(), "by_kind": collections.Counter(), "sleep_weight": 0.0}
        self.ctx = ssl.create_default_context()

    def count(self, kind):
        c = getattr(self.tl, "cnt", None)
        if c is None:
            c = self.tl.cnt = collections.Counter()
        c[kind] += 1

    def take_counts(self):
        c = getattr(self.tl, "cnt", None) or collections.Counter()
        self.tl.cnt = collections.Counter()
        return c

    def _conn(self, host, fresh=False):
        conns = getattr(self.tl, "conns", None)
        if conns is None:
            conns = self.tl.conns = {}
        if fresh and host in conns:
            try:
                conns[host].close()
            except Exception:
                pass
            del conns[host]
        if host not in conns:
            conns[host] = http.client.HTTPSConnection(host, timeout=self.timeout, context=self.ctx)
        return conns[host]

    def _gov(self, w):
        t0 = time.monotonic()
        while True:
            with self.lock:
                now = time.monotonic()
                fresh = now - self.used_t < 8.0
                if not fresh or self.used_w + w <= self.weight_cap:
                    if fresh:
                        self.used_w += w
                    break
            time.sleep(0.4)
        waited = time.monotonic() - t0
        if waited > 0.05:
            with self.lock:
                self.stats["sleep_weight"] += waited

    def _wait_ban(self):
        while True:
            d = self.ban_until - time.time()
            if d <= 0:
                return
            time.sleep(min(d, 5))

    def fetch(self, url, params=None, rl=None, weight=0, raw=False, kind="x"):
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        u = urllib.parse.urlsplit(url)
        host, path = u.netloc, u.path + ("?" + u.query if u.query else "")
        last = None
        for attempt in range(self.retries):
            self._wait_ban()
            if weight:
                self._gov(weight)
            if rl:
                rl.wait()
            try:
                conn = self._conn(host, fresh=attempt > 0)
                conn.request("GET", path, headers={"User-Agent": self.ua, "Accept": "*/*", "Accept-Encoding": "gzip"})
                r = conn.getresponse()
                body = r.read()
                code = r.status
                used = r.getheader("X-MBX-USED-WEIGHT-1M")
                ra = r.getheader("Retry-After")
                if r.getheader("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
            except Exception as e:
                last = e
                with self.lock:
                    self.stats["retry"] += 1
                time.sleep(min((2 ** attempt) * 0.5 + random.random(), 20))
                continue
            with self.lock:
                self.stats["codes"][code] += 1
                if used is not None:
                    self.used_w, self.used_t = int(used), time.monotonic()
                    self.max_used = max(self.max_used, self.used_w)
            if code == 200:
                with self.lock:
                    self.stats["req"] += 1
                    self.stats["bytes"] += len(body)
                    self.stats["by_kind"][kind] += 1
                self.count(kind)
                return body if raw else json.loads(body)
            last = HttpErr(code, body[:300].decode("utf-8", "replace"), url)
            if code in (400, 404):
                raise last
            with self.lock:
                self.stats["retry"] += 1
            if code in (418, 429):
                wait = int(ra) if (ra and ra.isdigit()) else 5 * (attempt + 1)
                if wait > 900:
                    raise RuntimeError("бан IP на %d с (HTTP %d): %s" % (wait, code, url[:120]))
                self.log("HTTP %d: пауза всех потоков на %d с" % (code, wait))
                self.ban_until = max(self.ban_until, time.time() + wait)
                with self.lock:
                    self.used_w, self.used_t = self.weight_cap, time.monotonic()
            else:
                time.sleep(min((2 ** attempt) * 0.5 + random.random(), 30))
        raise RuntimeError("запрос не удался после %d попыток: %s | %s" % (self.retries, url[:160], last))


# ----------------------------------------------------------------------------------------------------------------- данные
def kline_weight(limit):
    return 1 if limit < 100 else 2 if limit <= 499 else 5 if limit <= 1000 else 10


def day_floor(ms):
    return ms - ms % DAY_MS


def fmt_h(x):
    return str(int(x)) if abs(x - round(x)) < 1e-9 else ("%.2f" % x).rstrip("0").rstrip(".")


def main():
    ap = common_args("Binance USD-M / Aster: минутные свечи и фандинг всех перпетуалов", default_rate=8.0, default_workers=4)
    ap.add_argument("--exchange", required=True, choices=("binance", "aster"))
    ap.add_argument("--source", default="auto", choices=("auto", "rest", "archive"), help="auto: архив + REST на пропавшие сутки (binance); rest; archive")
    ap.add_argument("--kline-limit", type=int, default=499, help="limit REST klines (499 -> вес 2, 1000 -> 5, 1500 -> 10)")
    ap.add_argument("--weight-cap", type=int, default=1800, help="потолок X-MBX-USED-WEIGHT-1M (лимит биржи 2400)")
    ap.add_argument("--arch-rate", type=float, default=8.0, help="запросов/с к data.binance.vision")
    ap.add_argument("--fund-rate", type=float, default=1.4, help="запросов/с к fundingRate/fundingInfo (лимит биржи 500 за 5 мин)")
    ap.add_argument("--shard", default="", help="K/N: брать контракты с номером i %% N == K — несколько процессов на ядрах (упирается в GIL: ~1.6 с ЦП на контракт)")
    a = ap.parse_args()
    ex = a.exchange
    out = a.out
    start_ms, end_ms = parse_ts(a.start), parse_ts(a.end)
    src = a.source if ex == "binance" else "rest"
    ensure_dirs(out, ex)
    shard = tuple(int(x) for x in a.shard.split("/")) if a.shard else None
    log = make_logger(out, ex, "run" if not shard else "run-shard%d" % shard[0])
    log("старт %s окно %s .. %s источник=%s rate=%.1f workers=%d limit=%d weight-cap=%d" % (ex, a.start, a.end, src, a.rate, a.workers, a.kline_limit, a.weight_cap))
    net = Net(a.weight_cap, log)
    rl_api, rl_arch, rl_fund = RateLimiter(a.rate), RateLimiter(a.arch_rate), RateLimiter(a.fund_rate)
    base = FAPI[ex]
    now_ms = int(time.time() * 1000)

    # --- список контрактов
    info = net.fetch(base + "/fapi/v1/exchangeInfo", weight=20, rl=rl_api, kind="i")
    fund_h = {}
    try:
        for r in net.fetch(base + "/fapi/v1/fundingInfo", rl=rl_fund, kind="i"):
            fund_h[r["symbol"]] = fmt_h(float(r["fundingIntervalHours"]))
    except Exception as e:
        log("fundingInfo недоступен: %s" % e)
    insts, skipped = [], collections.Counter()
    for s in info["symbols"]:
        if s.get("contractType") not in ("PERPETUAL", "TRADIFI_PERPETUAL"):
            skipped["не перпетуал (%s)" % (s.get("contractType") or s.get("status"))] += 1
            continue
        onboard = int(s.get("onboardDate") or 0)
        dv = int(s.get("deliveryDate") or 0)
        if onboard >= end_ms:
            skipped["листинг после окна"] += 1
            continue
        if s.get("status") != "TRADING" and dv and dv < BIG_DELIVERY and dv < start_ms:
            skipped["снят до начала окна"] += 1
            continue
        i = build_instrument(ex, s, fund_h)
        if not i["funding_interval_h"]:
            i["funding_interval_h"] = "8"
            i["note"] = (i["note"] + "; " if i["note"] else "") + "интервал фандинга 8 по умолчанию (нет в fundingInfo)"
        insts.append(i)
    insts.sort(key=lambda i: i["symbol"])
    if not shard or shard[0] == 0:       # instruments.csv пишет один процесс
        write_instruments(out, ex, insts)
    cc = collections.Counter(i["class"] for i in insts)
    cs = collections.Counter(i["status"] for i in insts)
    log("контрактов в окне: %d; по классам %s; по статусам %s; пропущено %s" % (len(insts), dict(cc), dict(cs), dict(skipped)))
    if a.list_only:
        return

    sel = insts
    if a.symbols:
        want = {x.strip() for x in a.symbols.split(",") if x.strip()}
        sel = [i for i in insts if i["symbol"] in want]
        miss = want - {i["symbol"] for i in sel}
        if miss:
            log("нет среди контрактов окна: %s" % sorted(miss))
    if a.limit_symbols:
        sel = sel[:a.limit_symbols]
    if shard:
        sel = [i for j, i in enumerate(sel) if j % shard[1] == shard[0]]
    log("к закачке контрактов: %d" % len(sel))

    # --- свечи
    def parse_rest(js):
        return [(int(k[0]), k[1], k[2], k[3], k[4], k[5], k[7]) for k in js]

    def rest_range(sym, a_ms, b_ms):
        """Свечи [a_ms, b_ms) постранично (по возрастанию; ответ короче limit -> данные кончились)."""
        rows, cur = [], a_ms
        hi = min(b_ms, now_ms) - 1
        while cur <= hi:
            js = net.fetch(base + "/fapi/v1/klines", {"symbol": sym, "interval": "1m", "startTime": cur, "endTime": hi, "limit": a.kline_limit},
                           rl=rl_api, weight=kline_weight(a.kline_limit), kind="k")
            if not js:
                break
            rows += parse_rest(js)
            if len(js) < a.kline_limit:
                break
            cur = int(js[-1][0]) + MIN_MS
        return rows

    def parse_arch(raw):
        z = zipfile.ZipFile(io.BytesIO(raw))
        rows = []
        for line in z.read(z.namelist()[0]).decode().splitlines():
            p = line.split(",")
            if len(p) < 8 or not p[0].isdigit():
                continue
            t = int(p[0])
            if t > 10 ** 14:       # микросекунды
                t //= 1000
            rows.append((t, p[1], p[2], p[3], p[4], p[5], p[7]))
        return rows

    def life_days(inst):
        lo = max(day_floor(start_ms), day_floor(inst["listed_ms"] - DAY_MS) if inst["listed_ms"] else day_floor(start_ms))
        hi_t = min(end_ms, int(inst["delisted_ms"]) + 1 if inst["delisted_ms"] != "" else end_ms)
        return [d for d in range(lo, day_floor(hi_t - 1) + DAY_MS, DAY_MS) if d < end_ms]

    def archive_rows(sym0, days):
        sym = urllib.parse.quote(sym0)      # у Binance/Aster есть символы с китайскими иероглифами (币安人生USDT)
        today = day_floor(now_ms)
        want = [d for d in days if d < today]
        rows, bymonth = [], collections.OrderedDict()
        for d in want:
            dt = datetime.datetime.fromtimestamp(d / 1000, datetime.timezone.utc)
            bymonth.setdefault((dt.year, dt.month), []).append(d)
        for (y, m), ds in bymonth.items():
            ms0 = int(datetime.datetime(y, m, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
            ms1 = int(datetime.datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
            if ms0 >= start_ms and ms1 <= end_ms and ms1 <= today:       # месяц целиком внутри окна
                try:
                    rows += parse_arch(net.fetch("%s/monthly/klines/%s/1m/%s-1m-%d-%02d.zip" % (ARCH, sym, sym, y, m), rl=rl_arch, raw=True, kind="a"))
                    continue
                except HttpErr as e:
                    if e.code != 404:
                        raise
            for d in ds:
                dd = datetime.datetime.fromtimestamp(d / 1000, datetime.timezone.utc)
                try:
                    rows += parse_arch(net.fetch("%s/daily/klines/%s/1m/%s-1m-%s.zip" % (ARCH, sym, sym, dd.strftime("%Y-%m-%d")), rl=rl_arch, raw=True, kind="a"))
                except HttpErr as e:
                    if e.code != 404:
                        raise
        return rows

    def process(inst):
        sym = inst["symbol"]
        fn = safe_name(sym)
        if not a.force and is_done(out, ex, fn):
            return "пропуск (.done)"
        net.take_counts()
        t0 = time.time()
        days = life_days(inst)
        rows, filled = [], 0
        if days:
            if src in ("auto", "archive"):
                try:
                    rows = archive_rows(sym, days)
                except Exception as e:
                    log("%s: архив недоступен (%s) -> REST на весь период" % (sym, str(e)[:120]))
                    rows = []
                if src == "auto":
                    have = {day_floor(r[0]) for r in rows}
                    zero = [d for d in days if d not in have]
                    filled = len(zero)
                    k = 0
                    while k < len(zero):          # склеиваем подряд идущие сутки в один диапазон
                        j = k
                        while j + 1 < len(zero) and zero[j + 1] == zero[j] + DAY_MS:
                            j += 1
                        rows += rest_range(sym, zero[k], zero[j] + DAY_MS)
                        k = j + 1
            else:
                rows = rest_range(sym, days[0], min(end_ms, days[-1] + DAY_MS))
        closed = [r for r in rows if r[0] + MIN_MS <= now_ms]
        trim = 0
        if inst["listed_ms"]:
            # символ мог раньше принадлежать другому (снятому) контракту (Binance MDTUSDT: крипто-MDT -> акция MDT 21.10): месячный архив
            # хранит оба -> режем всё до минуты листинга текущего контракта
            before = len(closed)
            closed = [r for r in closed if r[0] >= inst["listed_ms"] // MIN_MS * MIN_MS]
            trim += before - len(closed)
        if inst["delisted_ms"] != "":
            # после снятия контракта архив/REST дают плоские свечи с нулевым объёмом до конца месяца: режем хвост после последней
            # свечи с объёмом (но не раньше момента снятия)
            dl = int(inst["delisted_ms"])
            lastv = max((r[0] for r in closed if r[0] <= dl + DAY_MS and float(r[5]) > 0), default=0)
            cut = max(dl, lastv)
            kept = [r for r in closed if r[0] <= cut]
            trim, closed = trim + len(closed) - len(kept), kept
        n, gaps = write_candles(os.path.join(out, ex, "candles", fn + ".csv.gz"), closed, start_ms=start_ms, end_ms=end_ms)
        bad = 0
        for r in closed:
            if start_ms <= r[0] < end_ms:
                o, h, l, c = float(r[1]), float(r[2]), float(r[3]), float(r[4])
                if not (o > 0 and h > 0 and l > 0 and c > 0 and h >= max(o, c) and l <= min(o, c)):
                    bad += 1
        # --- фандинг
        frows, cur = [], start_ms - DAY_MS
        while True:
            js = net.fetch(base + "/fapi/v1/fundingRate", {"symbol": sym, "startTime": cur, "endTime": end_ms + 3600_000, "limit": 1000}, rl=rl_fund, kind="f")
            frows += [(int(x["fundingTime"]), x["fundingRate"]) for x in js]
            if len(js) < 1000:
                break
            cur = int(js[-1]["fundingTime"]) + 1
        frows = sorted(set(frows))
        static = float(inst["funding_interval_h"] or 8)
        fout, ftrim = [], 0
        dl_ms = int(inst["delisted_ms"]) if inst["delisted_ms"] != "" else None
        for i, (t, rate) in enumerate(frows):
            tn = (t + 500) // 1000 * 1000
            if not (start_ms <= tn < end_ms):
                continue
            if dl_ms is not None and tn > dl_ms + 60_000:     # Aster после снятия продолжает отдавать «ставку по умолчанию» каждый час
                ftrim += 1
                continue
            if i > 0:
                ih = (t - frows[i - 1][0]) / 3_600_000
            elif len(frows) > 1:
                ih = (frows[1][0] - t) / 3_600_000
            else:
                ih = static
            ih = round(ih * 4) / 4 if 0.25 <= ih <= 24 else static
            fout.append((t, rate, fmt_h(ih)))
        nf = write_funding(os.path.join(out, ex, "funding", fn + ".csv.gz"), fout)
        c = net.take_counts()
        st = "свечей %d дыр %d fill_days %d trim %d bad %d фандинг %d ftrim %d req k=%d a=%d f=%d %.1fс" % (n, gaps, filled, trim, bad, nf, ftrim, c["k"], c["a"], c["f"], time.time() - t0)
        mark_done(out, ex, fn, st)
        return st

    res = run_parallel(sel, process, a.workers, log)
    s = net.stats
    log("запросов %d (по видам %s) повторов %d, коды %s, трафик %.1f МБ, макс. вес/мин %d, ждали вес %.0f с-потоков" %
        (s["req"], dict(s["by_kind"]), s["retry"], dict(s["codes"]), s["bytes"] / 1e6, net.max_used, s["sleep_weight"]))
    if res["fails"]:
        log("СБОИ (%d): %s" % (len(res["fails"]), res["fails"][:20]))
        sys.exit(2)


if __name__ == "__main__":
    main()
