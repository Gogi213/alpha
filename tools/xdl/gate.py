#!/usr/bin/env python3
"""Gate (USDT-перпетуалы, REST v4 + публичный архив download.gatedata.org): минутные свечи + история фандинга ВСЕХ контрактов
futures/usdt (крипта, акции/ETF, индексы, металлы, нефть/газ, валюта, пред-рынок) за окно [--start, --end) UTC. Ключей нет.

Источники (проверены пробой с Steam Deck 07.11.2026, см. отчёт docs/findings/xdl9-gate-2026-11-07.md):
  GET /api/v4/futures/usdt/contracts[/{name}]          — список активных / один контракт (в т. ч. снятый: status=delisted)
  GET /api/v4/futures/usdt/candlesticks                — свечи; НО отдаёт только последние 10000 точек от «сейчас» (≈ 6,94 суток для 1m:
                                                          «Candlestick too long ago. Maximum 10000 points recently are allowed»), ≤ 2000 точек за запрос,
                                                          from/to включительно; пустые минуты биржа рисует плоскими свечами v=0, а ПОСЛЕДНЯЯ точка ответа
                                                          с явным `to` — всегда фиктивная плоская свеча (чанк просит на минуту больше и её отбрасывает)
  GET /api/v4/futures/usdt/funding_rate                — история выплат фандинга (from/to, limit ≤ 1000), глубина не ограничена
  https://download.gatedata.org/futures_usdt/candlesticks_1m/<ГГГГММ>/<КОНТРАКТ>-<ГГГГММ>.csv.gz — месячный архив 1m, БЕЗ заголовка:
                                                          t(с), объём(контрактов), close, high, low, open; выкладывается после конца месяца
  https://download.gatedata.org/futures_usdt/funding_applies/<ГГГГММ>/…  — применённые выплаты фандинга (t(с), ставка), для сверки
  https://gateio-public-data.s3.amazonaws.com/?prefix=…  — листинг бакета (какие контракты есть в месяце, включая снятые)
Схема: месяцы окна, для которых архив уже выложен, берутся из архива (контракт есть в листинге месяца); месяцы без архива (идущий месяц) —
из REST /candlesticks (должно укладываться в 10000 минут от «сейчас»: иначе контракт НЕ помечается готовым). Фандинг — REST на всё окно
(+10 ч назад для интервала первой выплаты); архив funding_applies не качается при закачке, его сверка с REST — подкоманда --mode check.
Результат — формат xdl_common: <out>/gate/{candles,funding}/<КОНТРАКТ>.csv.gz, <out>/gate/instruments.csv (+колонки pre_listing, pre_listing_src,
как в ~/xdl9/inst/*.csv), метки _state/*.done; сырьё архива свечей — <out>/gate/_raw/candlesticks_1m/<ГГГГММ>/<КОНТРАКТ>.csv.gz (как отдал Gate).
Единицы и поля:
  * v = объём в БАЗОВЫХ единицах = контракты × quanto_multiplier (REST и архив отдают число контрактов; у акций это штуки акций, у BTC — монеты);
    contract_size в instruments.csv = quanto_multiplier; цена — как у Gate (строкой, без пересчёта).
  * quote_v ПУСТОЙ у всех строк: архив его не содержит (REST отдаёт `sum`, но смешивать источники в одной колонке не стали; `sum` используется
    только в проверке множителя --mode check).
  * время свечи t — начало минуты UTC (секунды Gate × 1000); минуты без строки не дорисовываются. Строка есть ⇔ в минуте были сделки: месячный архив
    содержит только такие минуты, поэтому плоские v=0, которыми REST заполняет пустые минуты, в файл не пишутся (число — rest_flat_dropped в метке .done).
  * interval_h фандинга — разрыв до предыдущей выплаты, округлён до 0,01 ч (REST интервал не отдаёт; метки Gate гуляют на ±1–2 с); для первой выплаты
    окна предыдущая берётся из запроса, расширенного на 10 ч назад; нет предыдущей — разрыв до следующей, нет и её — funding_interval контракта.
  * enable_decimal=true (дробный размер ордера): объём свечи Gate (REST и архив) — целое число контрактов, дробная часть отброшена (проверено по тиковому
    архиву сделок, --mode trades), поэтому v таких контрактов занижен, а минуты с одними дробными сделками имеют v=0; в instruments.csv это note.
  * класс — по метке биржи contract_type: stocks→equity (ETF биржа от акций не отделяет), indices→index, metals→metal, forex→fx (база-стейбл→crypto),
    commodities→energy для CL/BZ/NG/…, иначе commodity; пустой contract_type→crypto. is_pre_market=true → pre_listing=1 (первичное поле биржи).
Лимиты (факт, замер 07.11): публичные ручки отдают заголовки X-Gate-RateLimit-Limit=200 / Requests-Remain / Reset-Timestamp — окно ≈ 10 с, 200 запросов на
  ручку и IP, счётчик у каждой ручки свой (первый вызов каждой ручки даёт Remain=199). Документированный лимит по сайту Gate здесь не перепроверялся.
  Скрипт держит ≤ --rate запросов/с на все ручки вместе (по умолчанию 8 = 80 за 10 с, 40 % лимита), при Remain ≤ 25 ждёт сброса окна, на 429 —
  общая пауза всех потоков. Архив/листинг — отдельный ограничитель --arch-rate. В полном прогоне 07.11: 4291 REST-запрос, 0 ответов 429 и прочих HTTP-ошибок.
usage: gate.py --out DIR [--start 2026-10-01] [--end 2026-11-05] [--rate 8] [--arch-rate 10] [--workers 6] [--symbols BTC_USDT,ETH_USDT]
               [--limit-symbols N] [--list-only] [--force] [--mode download|check|finalize|audit|align|trades] [--check-sample N] [--check-seed S]
Режимы: download — закачка (по умолчанию); check — сверка с REST и архивом фандинга; trades --symbols A,B — пересборка минут из архива сделок;
        finalize — delisted_ms снятых по последней минуте; audit — сводка качества (F1); align — выравнивание времени с другими биржами."""
import os, sys, io, csv, gzip, json, time, random, threading, collections, datetime, urllib.request, urllib.parse, urllib.error
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xdl_common import (INSTR_FIELDS, MIN_MS, RateLimiter, common_args, ensure_dirs, is_done, make_logger, mark_done, ms_to_str, parse_ts,
                        run_parallel, safe_name, write_candles, write_funding)

EX = "gate"
API = "https://api.gateio.ws/api/v4"
ARCH = "https://download.gatedata.org/futures_usdt"
S3 = "https://gateio-public-data.s3.amazonaws.com/"
UA = "alpha-xdl/1"
REST_PTS = 2000                       # макс. точек свечей за запрос
REST_REACH_MIN = 10000                # глубина REST свечей 1m от «сейчас», минут
FUND_BACK_MS = 10 * 3600_000          # запас назад для интервала первой выплаты
INST_COLS = INSTR_FIELDS + ["pre_listing", "pre_listing_src"]
STABLE = {"USDC", "USDT", "USDE", "DAI", "FDUSD", "TUSD", "USD1", "PYUSD", "BUSD"}
ENERGY_TICKERS = {"CL", "BZ", "NG", "HO", "RB", "WTI", "BRENT"}


class GateError(RuntimeError):
    def __init__(self, label, msg="", code=0):
        super().__init__("%s %s (HTTP %s)" % (label, msg, code))
        self.label, self.msg, self.code = label, msg, code


class Client:
    """Публичный клиент Gate: REST (с учётом заголовков лимита, общая пауза на 429) и CDN/S3 архива."""
    def __init__(self, rate, arch_rate, log, timeout=40):
        self.api_rl, self.arch_rl = RateLimiter(rate), RateLimiter(arch_rate)
        self.timeout, self.log = timeout, log
        self.lock = threading.Lock()
        self.pause_until = 0.0
        self.stats = collections.Counter()

    # --- общая пауза всех потоков
    def _wait_pause(self):
        while True:
            w = self.pause_until - time.monotonic()
            if w <= 0:
                return
            time.sleep(min(w, 30))

    def _pause(self, sec):
        with self.lock:
            self.pause_until = max(self.pause_until, time.monotonic() + sec)

    def _hdr_throttle(self, hdr):
        try:
            remain = int(hdr.get("X-Gate-RateLimit-Requests-Remain", "999"))
            if remain <= 25:                                   # окно почти исчерпано: ждём его сброса (Reset-Timestamp — секунда сервера)
                reset = int(hdr.get("X-Gate-RateLimit-Reset-Timestamp", "0"))
                self._pause(min(max(reset - time.time(), 0.5) + 0.5, 11))
                with self.lock:
                    self.stats["hdr_pause"] += 1
        except (ValueError, TypeError):
            pass

    # --- REST
    def api(self, path, params=None):
        url = API + path + (("?" + urllib.parse.urlencode(params)) if params else "")
        last = None
        for attempt in range(8):
            self._wait_pause()
            self.api_rl.wait()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    body, hdr = r.read(), r.headers
                with self.lock:
                    self.stats["api_ok"] += 1
                self._hdr_throttle(hdr)
                return json.loads(body)
            except urllib.error.HTTPError as e:
                body = e.read()[:400]
                with self.lock:
                    self.stats["http_%d" % e.code] += 1
                try:
                    j = json.loads(body)
                    label, msg = j.get("label", ""), j.get("message", "")
                except ValueError:
                    label, msg = "", body.decode("utf-8", "replace")
                if e.code == 429:
                    sec = min(10 * (attempt + 1), 60)
                    self._pause(sec)
                    self.log("HTTP 429 (лимит) %s, общая пауза %d с" % (path, sec))
                    last = GateError("RATE_LIMIT", msg, 429)
                    continue
                if e.code in (400, 404):
                    raise GateError(label or "HTTP_%d" % e.code, msg, e.code)
                last = GateError(label or "HTTP_%d" % e.code, msg, e.code)
                time.sleep(min((2 ** attempt) * 0.5 + random.random(), 30))
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError) as e:
                last = e
                with self.lock:
                    self.stats["net_err"] += 1
                time.sleep(min((2 ** attempt) * 0.5 + random.random(), 30))
        raise GateError("RETRIES", "%s: %s" % (url[:160], last))

    # --- архив / листинг
    def raw(self, url):
        """GET bytes. None — файла нет (403/404: Gate/S3 отдаёт 403 на несуществующие ключи)."""
        last = None
        for attempt in range(6):
            self._wait_pause()
            self.arch_rl.wait()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=max(self.timeout, 120)) as r:
                    data = r.read()
                with self.lock:
                    self.stats["arch_ok"] += 1
                return data
            except urllib.error.HTTPError as e:
                with self.lock:
                    self.stats["arch_http_%d" % e.code] += 1
                if e.code in (403, 404):
                    return None
                last = e
                if e.code == 429:
                    self._pause(10 * (attempt + 1))
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last = e
                with self.lock:
                    self.stats["net_err"] += 1
            time.sleep(min((2 ** attempt) * 0.5 + random.random(), 30))
        raise GateError("ARCH_RETRIES", "%s: %s" % (url[:160], last))

    def s3_list(self, prefix):
        """{имя файла без каталога: размер} для префикса (пагинация по marker)."""
        out, marker = {}, ""
        ns = "{http://s3.amazonaws.com/doc/2006-03-01/}"
        while True:
            q = {"prefix": prefix, "max-keys": "1000"}
            if marker:
                q["marker"] = marker
            data = self.raw(S3 + "?" + urllib.parse.urlencode(q))
            if data is None:
                raise GateError("S3_LIST", prefix)
            root = ET.fromstring(data)
            keys = [(c.findtext(ns + "Key"), int(c.findtext(ns + "Size") or 0)) for c in root.findall(ns + "Contents")]
            for k, s in keys:
                out[k[len(prefix):]] = s
            if root.findtext(ns + "IsTruncated") != "true" or not keys:
                return out
            marker = keys[-1][0]


# ---------------------------------------------------------------------------------------------------------------- месяцы окна
def month_list(start_ms, end_ms):
    d = datetime.datetime.fromtimestamp(start_ms / 1000, datetime.timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    out = []
    while int(d.timestamp() * 1000) < end_ms:
        nd = (d.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        out.append((d.strftime("%Y%m"), int(d.timestamp() * 1000), int(nd.timestamp() * 1000)))
        d = nd
    return out


def arch_names(listing, ym):
    """{контракт: размер} из листинга месяца (ключи вида <КОНТРАКТ>-<ГГГГММ>.csv.gz)."""
    suf = "-%s.csv.gz" % ym
    return {k[: -len(suf)]: s for k, s in listing.items() if k.endswith(suf)}


# ---------------------------------------------------------------------------------------------------------------- справочник
def classify(c):
    """-> (class, class_src, note). Метка биржи contract_type; ничего не угадываем молча."""
    name = c["name"]
    base = name.rsplit("_", 1)[0]
    ct = (c.get("contract_type") or "").strip()
    if ct == "stocks":
        return "equity", "contract_type=stocks", "ETF биржа от акций не отделяет"
    if ct == "indices":
        return "index", "contract_type=indices", ""
    if ct == "metals":
        return "metal", "contract_type=metals", ""
    if ct == "forex":
        if base in STABLE:
            return "crypto", "contract_type=forex+base=stablecoin", "помечен forex, но базовый актив — стейбл-коин"
        return "fx", "contract_type=forex", ""
    if ct == "commodities":
        if base.upper() in ENERGY_TICKERS:
            return "energy", "contract_type=commodities+ticker", ""
        return "commodity", "contract_type=commodities", "подкласс (металл/энергия) не определён по тикеру"
    if ct == "":
        return "crypto", "default(contract_type empty)", ""
    return "other", "contract_type=%s (неизвестное значение)" % ct, "неизвестный contract_type — класс не определён"


def to_row(c, src_note=""):
    name = c["name"]
    base, quote = name.rsplit("_", 1)
    cls, src, cnote = classify(c)
    notes = [cnote] if cnote else []
    if src_note:
        notes.append(src_note)
    pre = 1 if c.get("is_pre_market") else 0
    if pre:
        notes.append("is_pre_market=true (пред-рынок до листинга базового актива)")
    if c.get("in_delisting"):
        notes.append("in_delisting=true")
    if c.get("enable_decimal"):
        notes.append("enable_decimal=true (дробный размер ордера; объём свечи у Gate — целое число контрактов, дробная часть теряется)")
    delisted = ""
    if c.get("status") == "delisted":
        delisted = int(c.get("config_change_time") or 0) * 1000 or ""
        notes.append("delisted_ms = config_change_time контракта (точный момент снятия API не отдаёт)")
    if c.get("type") not in (None, "direct"):
        notes.append("type=%s (не линейный)" % c.get("type"))
    fi = int(c.get("funding_interval") or 0)
    return {"exchange": EX, "symbol": safe_name(name), "native_id": name, "base": base, "quote": quote, "settle": "USDT",
            "class": cls, "class_src": src, "status": c.get("status", ""), "contract_size": c.get("quanto_multiplier", ""),
            "tick_size": c.get("order_price_round", ""), "listed_ms": int(c.get("launch_time") or 0) * 1000 or "", "delisted_ms": delisted,
            "funding_interval_h": (fi / 3600.0) if fi else "", "taker_fee_bps": "", "maker_fee_bps": "", "note": "; ".join(notes),
            "pre_listing": pre, "pre_listing_src": "gate is_pre_market=true" if pre else "", "_raw": c}


def write_inst(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=INST_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in INST_COLS})
    os.replace(tmp, path)


def fetch_universe(cl, months, start, end, log):
    """-> (rows, listings{ym: {имя: размер}}, notes). Контракты = архив свечей окна ∪ активные REST, запущенные до конца окна."""
    act = {c["name"]: c for c in cl.api("/futures/usdt/contracts")}
    log("REST /contracts: %d активных" % len(act))
    lst = {}
    for ym, _, _ in months:
        lst[ym] = arch_names(cl.s3_list("futures_usdt/candlesticks_1m/%s/" % ym), ym)
        log("архив свечей %s: %d контрактов, %.1f МБ" % (ym, len(lst[ym]), sum(lst[ym].values()) / 1048576.0))
    names = set(act)
    for ym in lst:
        names |= set(lst[ym])
    rows, excluded = [], collections.Counter()
    for n in sorted(names):
        c = act.get(n)
        note = ""
        if c is None:                                          # снят: в списке активных нет, архив его знает
            try:
                c = cl.api("/futures/usdt/contracts/" + urllib.parse.quote(n))
            except GateError as e:
                log("ВНИМАНИЕ: нет метаданных %s: %s" % (n, e))
                excluded["нет метаданных"] += 1
                continue
            note = "нет в списке активных; метаданные из /contracts/{name}"
        if int(c.get("launch_time") or 0) * 1000 >= end:
            excluded["листинг после конца окна"] += 1
            log("вне окна (листинг %s): %s" % (ms_to_str(int(c["launch_time"]) * 1000), n))
            continue
        rows.append(to_row(c, note))
    return rows, lst, excluded


# ---------------------------------------------------------------------------------------------------------------- свечи
def dec_str(x):
    s = format(x, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def vol_base(v, q):
    """объём в контрактах (str|int|float) × quanto (Decimal) → строка."""
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        return None
    return dec_str(d * q) if q != 1 else dec_str(d)


def parse_arch(data, q):
    """Архивный CSV (gz, без заголовка): t,vol,close,high,low,open → ([(t_ms,o,h,l,c,v,None)], плохих строк)."""
    out, bad = [], 0
    zero_cache = {}
    for line in gzip.decompress(data).decode("utf-8").splitlines():
        if not line:
            continue
        a = line.split(",")
        if len(a) != 6 or not a[0].isdigit():
            bad += 1
            continue
        vs = zero_cache.get(a[1])
        if vs is None:
            vs = vol_base(a[1], q)
            if vs is None:
                bad += 1
                continue
            if len(zero_cache) < 5000:
                zero_cache[a[1]] = vs
        out.append((int(a[0]) * 1000, a[5], a[3], a[4], a[2], vs, None))
    return out, bad


def fetch_rest_raw(cl, name, lo_ms, hi_ms):
    """REST свечи [lo_ms, hi_ms) -> (список сырых записей Gate, число запросов).
    ВАЖНО (установлено 07.11 сравнением двух сдвинутых запросов): последняя точка ответа с явным `to` у Gate — ФИКТИВНАЯ плоская свеча
    (o=h=l=c=close предыдущей, v=0), настоящая свеча той же минуты приходит, когда точка не последняя. Поэтому каждый чанк просит `to`
    на минуту дальше нужного (точка t=to — мусор) и оставляет только t < to: чанк даёт ≤ 1999 настоящих минут."""
    out, nreq, f = [], 0, lo_ms // MIN_MS * MIN_MS
    while f < hi_ms:
        to = min(f + (REST_PTS - 1) * MIN_MS, hi_ms)
        lst = cl.api("/futures/usdt/candlesticks", {"contract": name, "interval": "1m", "from": f // 1000, "to": to // 1000})
        nreq += 1
        out.extend(k for k in lst if f <= int(k["t"]) * 1000 < to)
        f = to
    return out, nreq


def fetch_rest_candles(cl, name, q, lo_ms, hi_ms):
    """REST свечи [lo_ms, hi_ms) в формате строк xdl (v в базовых единицах) -> (rows, число запросов, отброшено плоских v=0).
    REST дорисовывает минуты без сделок плоскими свечами v=0 (o=h=l=c=прежний close), месячный архив их НЕ содержит (в нём только минуты со сделками:
    у AAPL 44044 строки = 44044 минуты со сделками в архиве сделок). Чтобы окно было однородным (строка есть ⇔ в минуте были сделки), плоские v=0
    из REST не пишутся; в панелях v=0 и дыра всё равно дают NaN."""
    lst, nreq = fetch_rest_raw(cl, name, lo_ms, hi_ms)
    rows, flat = [], 0
    for k in lst:
        t = int(k["t"]) * 1000
        v = vol_base(k.get("v", 0), q)
        if v is not None and Decimal(v) == 0:
            flat += 1
            continue
        rows.append((t, k["o"], k["h"], k["l"], k["c"], v if v is not None else "", None))
    return rows, nreq, flat


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


def get_archive(cl, out, kind, ym, name, force=False):
    """Месячный файл архива (сырьё хранится как отдал Gate). -> bytes | None."""
    d = os.path.join(out, EX, "_raw", kind, ym)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, safe_name(name) + ".csv.gz")
    if not force and os.path.exists(p) and os.path.getsize(p) > 0:
        try:
            with open(p, "rb") as f:
                data = f.read()
            gzip.decompress(data)
            return data
        except (OSError, EOFError, ValueError):
            pass
    url = "%s/%s/%s/%s-%s.csv.gz" % (ARCH, kind, ym, urllib.parse.quote(name), ym)
    data = cl.raw(url)
    if data is None:
        return None
    gzip.decompress(data)                                      # целостность gzip; исключение = файл битый, не сохраняем
    with open(p + ".tmp", "wb") as f:
        f.write(data)
    os.replace(p + ".tmp", p)
    return data


# ---------------------------------------------------------------------------------------------------------------- фандинг
def fetch_funding(cl, c, start_ms, end_ms):
    """Выплаты [start, end) + интервал. -> (rows[(time_ms, rate, interval_h)], запросов, подстановок interval, число записей-предшественниц)."""
    name = c["name"]
    fi_h = (int(c.get("funding_interval") or 0) / 3600.0) or None
    lo_s = (start_ms - FUND_BACK_MS) // 1000
    got, nreq, cur_to = {}, 0, end_ms // 1000 - 1
    while cur_to >= lo_s:
        lst = cl.api("/futures/usdt/funding_rate", {"contract": name, "from": lo_s, "to": cur_to, "limit": 1000})
        nreq += 1
        for k in lst:
            got[int(k["t"]) * 1000] = k["r"]
        if len(lst) < 1000:
            break
        cur_to = min(int(k["t"]) for k in lst) - 1
    ts = sorted(got)
    out, fb = [], 0
    for i, t in enumerate(ts):
        if t < start_ms or t >= end_ms:
            continue
        if i > 0:
            iv = (t - ts[i - 1]) / 3.6e6
        elif len(ts) > 1:
            iv = (ts[1] - t) / 3.6e6
        else:
            iv = None
        if iv is None or iv < 0.9 or iv > 8.01:
            iv, fb = fi_h, fb + 1
        out.append((t, got[t], round(iv, 2) if iv is not None else ""))       # метки Gate «гуляют» на ±1–2 с (7,9997 / 8,0003 ч): округление до 0,01 ч
    return out, nreq, fb, sum(1 for t in ts if t < start_ms)


# ---------------------------------------------------------------------------------------------------------------- один контракт
def one(item, a, start, end, cl, months, lst):
    c, name, sym = item["_raw"], item["native_id"], item["symbol"]
    q = Decimal(str(c.get("quanto_multiplier") or "1"))
    now_closed = int(time.time() * 1000) // MIN_MS * MIN_MS
    eff_end = min(end, now_closed)
    floor_ms = (int(c.get("launch_time") or 0) * 1000) // MIN_MS * MIN_MS
    rows, src = {}, collections.Counter()
    rest_rng, bad_lines, notes = [], 0, []
    for ym, m0, m1 in months:
        lo, hi = max(start, m0, floor_ms), min(eff_end, m1)
        if min(end, m1) <= max(start, m0) or lo >= hi:
            continue
        if name in lst[ym]:
            data = get_archive(cl, a.out, "candlesticks_1m", ym, name)
            if data is None:
                raise GateError("ARCH_MISSING", "%s %s: в листинге есть, файла нет" % (name, ym))
            part, bad = parse_arch(data, q)
            bad_lines += bad
            n0 = len(rows)
            for r in part:
                if lo <= r[0] < hi:
                    rows[r[0]] = r
            src["arch_" + ym] += len(rows) - n0
        elif lst[ym]:                                           # месяц выложен, контракта в нём нет, хотя по дате листинга он должен торговаться
            notes.append("нет в архиве %s" % ym)
            rest_rng.append((lo, hi))
        else:                                                   # архива месяца нет (идущий месяц) — REST
            rest_rng.append((lo, hi))
    nreq_c, flat_dropped = 0, 0
    reach_ms = now_closed - (REST_REACH_MIN - 5) * MIN_MS
    for lo, hi in rest_rng:
        if hi <= lo:
            continue
        if lo < reach_ms:
            if c.get("status") == "delisted":                   # REST у снятого контракта пуст; глубже его досягаемости не спрашиваем
                notes.append("REST не запрашивался с %s (снят)" % ms_to_str(lo))
                continue
            raise GateError("OUT_OF_REACH", "%s: REST свечи не достают до %s (глубина %d минут от «сейчас»)" % (name, ms_to_str(lo), REST_REACH_MIN))
        part, n, fl = fetch_rest_candles(cl, name, q, lo, hi)
        nreq_c += n
        flat_dropped += fl
        for r in part:
            rows[r[0]] = r
        src["rest"] += len(part)
    crows = list(rows.values())
    ncand, gaps = write_candles(os.path.join(a.out, EX, "candles", sym + ".csv.gz"), crows, start_ms=start, end_ms=eff_end)
    frows, nf, fb, npre = fetch_funding(cl, c, start, end)
    nfund = write_funding(os.path.join(a.out, EX, "funding", sym + ".csv.gz"), frows)
    info = "candles=%d gaps=%d bad_ohlc=%d bad_lines=%d src=%s rest_flat_dropped=%d funding=%d iv_fallback=%d req_candles=%d req_funding=%d%s" % (
        ncand, gaps, bad_ohlc(crows), bad_lines, ",".join("%s:%d" % kv for kv in sorted(src.items())), flat_dropped, nfund, fb, nreq_c, nf,
        (" notes=" + "|".join(notes)) if notes else "")
    mark_done(a.out, EX, sym, info)
    return info


# ---------------------------------------------------------------------------------------------------------------- проверки (--mode check)
def read_candles(path):
    out = {}
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        next(f)
        for line in f:
            a = line.rstrip("\r\n").split(",")
            out[int(a[0])] = a
    return out


def check(a, cl, rows, months, lst, start, end, log):
    """Сверка независимыми источниками: (1) REST свечи на глубину ≈ 6,9 суток от «сейчас» против записанных файлов по ВСЕМ контрактам
    (5 полей + множитель через `sum`), (2) фандинг записанный (REST) против архива funding_applies за полные месяцы архива, ВСЕ контракты,
    (3) выборка --check-sample контрактов × 2 минуты: одна свеча в зоне REST, одна в зоне архива (если REST достаёт — сверка, иначе пропуск).
    Вывод: журнал + CSV <out>/gate/_logs/check-*.csv."""
    random.seed(a.check_seed)
    now_closed = int(time.time() * 1000) // MIN_MS * MIN_MS
    lo_ms = max(start, now_closed - (REST_REACH_MIN - 120) * MIN_MS)       # запас 2 ч: граница досягаемости REST уходит вперёд, пока идёт проверка
    arch_end = max([m1 for ym, m0, m1 in months if lst[ym]] or [start])      # сравнение REST ↔ записанное — только в зоне АРХИВА (в зоне REST источник тот же)
    hi_ms = min(end, now_closed, arch_end)
    res = {"files": 0, "c_common": 0, "c_only_file": 0, "c_only_rest": 0, "c_only_rest_flat": 0, "diff_o": 0, "diff_h": 0, "diff_l": 0, "diff_c": 0, "diff_v": 0,
           "mult_bad": 0, "mult_checked": 0, "f_common": 0, "f_only_file": 0, "f_only_arch": 0, "f_diff": 0, "f_contracts": 0, "f_contracts_diff": 0}
    per = []
    ex_diffs = []
    lock = threading.Lock()

    def worker(r):
        c, name, sym = r["_raw"], r["native_id"], r["symbol"]
        q = Decimal(str(c.get("quanto_multiplier") or "1"))
        p = os.path.join(a.out, EX, "candles", sym + ".csv.gz")
        if not os.path.exists(p):
            return
        mine = read_candles(p)
        loc = collections.Counter()
        floor_ms = (int(c.get("launch_time") or 0) * 1000) // MIN_MS * MIN_MS
        lo = max(lo_ms, floor_ms)
        if lo < hi_ms and c.get("status") != "delisted":
            f = lo
            while f < hi_ms:
                to = min(f + (REST_PTS - 1) * MIN_MS, hi_ms)
                lstv, _n = fetch_rest_raw(cl, name, f, to)
                got = {}
                for k in lstv:
                    got[int(k["t"]) * 1000] = k
                for t, k in got.items():
                    m = mine.get(t)
                    if m is None:
                        if Decimal(str(k.get("v", 0))) == 0:
                            loc["only_rest_flat"] += 1              # плоская дорисованная REST-минута без сделок: в файле её быть не должно
                        else:
                            loc["only_rest"] += 1                   # минута со сделками есть в REST и нет в файле — потеря
                        continue
                    loc["common"] += 1
                    v = vol_base(k.get("v", 0), q)
                    for key, j, kk in (("o", 1, "o"), ("h", 2, "h"), ("l", 3, "l"), ("c", 4, "c")):
                        if Decimal(m[j]) != Decimal(k[kk]):
                            loc["diff_" + key] += 1
                    if Decimal(m[5]) != Decimal(v):
                        loc["diff_v"] += 1
                    vc = Decimal(str(k.get("v", 0)))
                    if vc > 0 and k.get("sum") not in (None, ""):         # VWAP минуты = sum / (контракты × q) должен лежать в [low, high] с допуском 2 % (sum у Gate неточна на ~0,1 %); ошибка множителя ≥ ×2 ловится
                        loc["mult_checked"] += 1
                        vw = Decimal(k["sum"]) / (vc * q)
                        if not (Decimal(k["l"]) * Decimal("0.98") <= vw <= Decimal(k["h"]) * Decimal("1.02")):
                            loc["mult_bad"] += 1
                for t in range(f, to, MIN_MS):
                    if t in mine and t not in got:
                        loc["only_file"] += 1
                f = to
        # фандинг: записанный (REST) против архива funding_applies за месяцы с архивом
        fl = collections.Counter()
        fp = os.path.join(a.out, EX, "funding", sym + ".csv.gz")
        if os.path.exists(fp):
            with gzip.open(fp, "rt", encoding="utf-8", newline="") as f2:
                next(f2)
                mf = {}
                for line in f2:
                    z = line.rstrip("\r\n").split(",")
                    mf[int(z[0])] = z[1]
            for ym, m0, m1 in months:
                if name not in a.fund_arch.get(ym, {}):
                    continue
                data = get_archive(cl, a.out, "funding_applies", ym, name)
                if data is None:
                    continue
                arch = {}
                for line in gzip.decompress(data).decode().splitlines():
                    z = line.split(",")
                    if len(z) >= 2 and z[0].isdigit():
                        arch[int(z[0]) * 1000] = z[1]
                arch = {t: v for t, v in arch.items() if start <= t < end}
                mine_m = {t: v for t, v in mf.items() if m0 <= t < m1}
                for t, v in arch.items():
                    if t in mine_m:
                        fl["common"] += 1
                        if Decimal(v) != Decimal(mine_m[t]):
                            fl["diff"] += 1
                    else:
                        fl["only_arch"] += 1
                fl["only_file"] += sum(1 for t in mine_m if t not in arch)
        with lock:
            res["files"] += 1
            res["c_common"] += loc["common"]
            res["c_only_file"] += loc["only_file"]
            res["c_only_rest"] += loc["only_rest"]
            res["c_only_rest_flat"] += loc["only_rest_flat"]
            for k in ("o", "h", "l", "c", "v"):
                res["diff_" + k] += loc["diff_" + k]
            res["mult_bad"] += loc["mult_bad"]
            res["mult_checked"] += loc["mult_checked"]
            res["f_common"] += fl["common"]
            res["f_only_file"] += fl["only_file"]
            res["f_only_arch"] += fl["only_arch"]
            res["f_diff"] += fl["diff"]
            res["f_contracts"] += 1 if fl else 0
            res["f_contracts_diff"] += 1 if (fl["diff"] or fl["only_file"] or fl["only_arch"]) else 0
            per.append([name, loc["common"], loc["only_file"], loc["only_rest"], loc["only_rest_flat"], loc["diff_o"], loc["diff_h"], loc["diff_l"], loc["diff_c"],
                        loc["diff_v"], loc["mult_checked"], loc["mult_bad"], fl["common"], fl["only_file"], fl["only_arch"], fl["diff"]])

    a.fund_arch = {ym: arch_names(cl.s3_list("futures_usdt/funding_applies/%s/" % ym), ym) for ym, _, _ in months}
    log("check: REST свечи %s … %s, архив funding_applies: %s" % (ms_to_str(lo_ms), ms_to_str(hi_ms), {k: len(v) for k, v in a.fund_arch.items()}))
    run_parallel(rows, lambda r: (worker(r) or "ok"), a.workers, lambda m: log(m) if ("СБОЙ" in m or m.startswith("итого")) else None)
    p = os.path.join(a.out, EX, "_logs", "check-%s.csv" % datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S"))
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["contract", "c_common", "c_only_file", "c_only_rest", "c_only_rest_flat", "diff_o", "diff_h", "diff_l", "diff_c", "diff_v", "mult_checked",
                    "mult_bad", "f_common", "f_only_file", "f_only_arch", "f_diff"])
        w.writerows(sorted(per))
    log("check ИТОГ: " + json.dumps(res, ensure_ascii=False))
    log("check: построчно %s" % p)
    # выборка: две свечи по контракту (REST-зона и зона архива) — по одной минуте через REST
    sample = random.sample(rows, min(a.check_sample, len(rows)))
    for r in sample:
        c, name, sym = r["_raw"], r["native_id"], r["symbol"]
        q = Decimal(str(c.get("quanto_multiplier") or "1"))
        mine = read_candles(os.path.join(a.out, EX, "candles", sym + ".csv.gz"))
        ts_ = sorted(mine)
        for label, pool in (("зона архива, достижимая REST (31.10)", [t for t in ts_ if lo_ms <= t < arch_end]),
                            ("зона REST (с 01.11)", [t for t in ts_ if t >= arch_end])):
            if not pool:
                continue
            t = random.choice(pool)
            try:
                lstv = [x for x in cl.api("/futures/usdt/candlesticks", {"contract": name, "interval": "1m", "from": t // 1000, "to": t // 1000 + 60})
                        if int(x["t"]) * 1000 == t]
            except GateError as e:
                log("check минута %s %s %s: REST недоступен (%s)" % (name, label, ms_to_str(t), e.label))
                continue
            if not lstv:
                log("check минута %s %s %s: REST пусто" % (name, label, ms_to_str(t)))
                continue
            k, m = lstv[0], mine[t]
            v = vol_base(k.get("v", 0), q)
            same = all(Decimal(m[j]) == Decimal(k[kk]) for j, kk in ((1, "o"), (2, "h"), (3, "l"), (4, "c"))) and Decimal(m[5]) == Decimal(v)
            log("check минута %s %s %s: файл o=%s h=%s l=%s c=%s v=%s | REST o=%s h=%s l=%s c=%s v=%s sum=%s → %s" % (
                name, label, ms_to_str(t), m[1], m[2], m[3], m[4], m[5], k["o"], k["h"], k["l"], k["c"], v, k.get("sum"), "СОВПАЛО" if same else "РАСХОЖДЕНИЕ"))
    return res


# ---------------------------------------------------------------------------------------------------------------- finalize / audit / align (без сети)
def load_inst(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def last_candle_ms(path):
    last = None
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        next(f)
        for line in f:
            last = line
    return int(last.split(",", 1)[0]) if last else None


def finalize(a, log):
    """delisted_ms снятых контрактов: Gate момент снятия по API не отдаёт (config_change_time — момент смены настроек, не снятия: у VELODROME он
    на 1,7 суток раньше последней свечи, у WBD на 1 ч раньше), поэтому ставим КОНЕЦ последней минуты в файле свечей (измерено, не придумано)."""
    p = os.path.join(a.out, EX, "instruments.csv")
    rows = load_inst(p)
    n = 0
    for r in rows:
        if r["status"] != "delisted":
            continue
        cp = os.path.join(a.out, EX, "candles", r["symbol"] + ".csv.gz")
        t = last_candle_ms(cp) if os.path.exists(cp) else None
        if t is None:
            continue
        r["delisted_ms"] = t + MIN_MS
        keep = [x for x in r["note"].split("; ") if x and not x.startswith("delisted_ms")]
        keep.append("delisted_ms = конец последней минуты в файле свечей (Gate момент снятия по API не отдаёт)")
        r["note"] = "; ".join(keep)
        n += 1
        log("снят %s: последняя минута %s → delisted_ms=%s" % (r["native_id"], ms_to_str(t), ms_to_str(t + MIN_MS)))
    write_inst(p, rows)
    log("finalize: instruments.csv обновлён, снятых с delisted_ms: %d" % n)


def audit(a, start, end, log):
    """Сводка качества по записанным файлам (аналог F1): строки, дубли, порядок, сетка, дыры, v=0, плохие бары, выбросы цены (пороги — для обзора,
    не критерий: минутная доходность |ln(c_t/c_{t-1})| > 20 %, тень бара > 20 % от тела), фандинг: интервалы и их смена, края окна."""
    import math
    rows = load_inst(os.path.join(a.out, EX, "instruments.csv"))
    nb = (end - start) // MIN_MS
    out = []
    tot = collections.Counter()
    cls_cnt = collections.defaultdict(collections.Counter)
    jumps, ivchg, nofund = [], [], []
    for r in rows:
        sym = r["symbol"]
        cp = os.path.join(a.out, EX, "candles", sym + ".csv.gz")
        if not os.path.exists(cp):
            tot["no_file"] += 1
            out.append([sym, r["class"], r["status"], 0] + [""] * 16)
            continue
        ts, bars, dup, unordered, offgrid, outwin, v0, bad = [], [], 0, 0, 0, 0, 0, 0
        prev = None
        with gzip.open(cp, "rt", encoding="utf-8", newline="") as f:
            next(f)
            for line in f:
                z = line.rstrip("\r\n").split(",")
                t = int(z[0])
                if t % MIN_MS:
                    offgrid += 1
                if t < start or t >= end:
                    outwin += 1
                if prev is not None:
                    if t == prev:
                        dup += 1
                    elif t < prev:
                        unordered += 1
                prev = t
                try:
                    o, h, l, c, v = float(z[1]), float(z[2]), float(z[3]), float(z[4]), float(z[5])
                except ValueError:
                    bad += 1
                    continue
                if v == 0:
                    v0 += 1
                if not (o > 0 and h > 0 and l > 0 and c > 0) or h < max(o, c) or l > min(o, c):
                    bad += 1
                ts.append(t)
                bars.append((o, h, l, c, v))
        n = len(ts)
        first, last = (ts[0], ts[-1]) if n else (None, None)
        gaps = ((last - first) // MIN_MS + 1 - n) if n else 0
        head = ((first - start) // MIN_MS) if n else nb
        tail = ((end - MIN_MS - last) // MIN_MS) if n else 0
        nj = nw = 0
        worst = (0.0, None)
        for i in range(1, n):
            if ts[i] - ts[i - 1] == MIN_MS and bars[i][3] > 0 and bars[i - 1][3] > 0:
                x = abs(math.log(bars[i][3] / bars[i - 1][3]))
                if x > 0.2:
                    nj += 1
                if x > worst[0]:
                    worst = (x, ts[i])
        for (o, h, l, c, v) in bars:
            body_hi, body_lo = max(o, c), min(o, c)
            if body_hi > 0 and body_lo > 0 and (h / body_hi > 1.2 or l / body_lo < 0.8):
                nw += 1
        if nj or nw:
            jumps.append((sym, nj, nw, worst[0], ms_to_str(worst[1]) if worst[1] else ""))
        # фандинг
        fp = os.path.join(a.out, EX, "funding", sym + ".csv.gz")
        fts, fiv = [], []
        if os.path.exists(fp):
            with gzip.open(fp, "rt", encoding="utf-8", newline="") as f:
                next(f)
                for line in f:
                    z = line.rstrip("\r\n").split(",")
                    fts.append(int(z[0]))
                    fiv.append(z[2])
        ivset = sorted(set(fiv))
        if len(ivset) > 1:
            ivchg.append((sym, ivset))
        if not fts:
            nofund.append(sym)
        fgap_bad = sum(1 for i in range(1, len(fts)) if fts[i] <= fts[i - 1])
        out.append([sym, r["class"], r["status"], n, first and ms_to_str(first), last and ms_to_str(last), gaps, head, tail, dup, unordered,
                    offgrid, outwin, v0, bad, nj, nw, len(fts), "|".join(ivset), fgap_bad])
        tot["files"] += 1
        tot["rows"] += n
        tot["v0"] += v0
        tot["dup"] += dup
        tot["unordered"] += unordered
        tot["offgrid"] += offgrid
        tot["outwin"] += outwin
        tot["bad"] += bad
        tot["gap_min"] += gaps
        tot["full"] += (n == nb)
        tot["gap_files"] += (gaps > 0)
        tot["head_files"] += (head > 0)
        tot["tail_files"] += (tail > 0)
        tot["fund_rows"] += len(fts)
        tot["fund_unordered"] += fgap_bad
        c = cls_cnt[r["class"]]
        c["contracts"] += 1
        c["rows"] += n
        c["v0"] += v0
        c["full"] += (n == nb)
        c["gap_min"] += gaps
    p = os.path.join(a.out, EX, "_logs", "audit-%s.csv" % datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S"))
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["symbol", "class", "status", "rows", "first_utc", "last_utc", "gap_min", "missing_head", "missing_tail", "dup", "unordered",
                    "off_grid", "out_win", "v0", "bad_ohlc", "ret_gt20pct", "wick_gt20pct", "fund_rows", "fund_iv_h", "fund_unordered"])
        w.writerows(out)
    log("audit: контрактов в instruments.csv %d, файлов свечей %d, без файла %d" % (len(rows), tot["files"], tot["no_file"]))
    log("audit: строк %d (при полных рядах было бы %d), полных (%d баров) %d, v=0 %d (%.1f%%), дубли %d, нарушения порядка %d, не на сетке %d, "
        "вне окна %d, плохих баров %d" % (tot["rows"], tot["files"] * nb, nb, tot["full"], tot["v0"], 100.0 * tot["v0"] / max(tot["rows"], 1),
                                          tot["dup"], tot["unordered"], tot["offgrid"], tot["outwin"], tot["bad"]))
    log("audit: дыры внутри ряда: %d минут в %d файлах; начало окна без свечей (листинг позже): %d файлов; хвост окна без свечей: %d файлов" % (
        tot["gap_min"], tot["gap_files"], tot["head_files"], tot["tail_files"]))
    log("audit: фандинг: строк %d, нарушений порядка/дублей %d, контрактов без выплат %d %s" % (tot["fund_rows"], tot["fund_unordered"], len(nofund), nofund[:10]))
    log("audit: фандинг со сменой интервала в окне: %d контрактов %s" % (len(ivchg), ivchg[:8]))
    log("audit: контрактов с |доходность минуты| > 20 %% или тенью > 20 %%: %d" % len(jumps))
    for j in sorted(jumps, key=lambda x: -x[3])[:15]:
        log("  выброс %s: минут с ret>20%% %d, баров с тенью>20%% %d, max |ln| %.3f в %s" % j)
    for k, v in sorted(cls_cnt.items()):
        log("  класс %-9s контрактов %4d, строк %9d, v=0 %8d (%.1f%%), полных %4d, дыр %d мин" % (
            k, v["contracts"], v["rows"], v["v0"], 100.0 * v["v0"] / max(v["rows"], 1), v["full"], v["gap_min"]))
    log("audit: построчно %s" % p)


ALIGN_NAMES = {
    "BTC": {"gate": "BTC_USDT", "binance": "BTCUSDT", "bybit": "BTCUSDT", "okx": "BTC-USDT-SWAP", "bingx": "BTC-USDT", "bitget": "BTCUSDT",
            "aster": "BTCUSDT", "kraken": "PF_XBTUSD", "lighter": "BTC"},
    "ETH": {"gate": "ETH_USDT", "binance": "ETHUSDT", "bybit": "ETHUSDT", "okx": "ETH-USDT-SWAP", "bingx": "ETH-USDT", "bitget": "ETHUSDT",
            "aster": "ETHUSDT", "kraken": "PF_ETHUSD", "lighter": "ETH"},
    "SOL": {"gate": "SOL_USDT", "binance": "SOLUSDT", "bybit": "SOLUSDT", "okx": "SOL-USDT-SWAP", "bingx": "SOL-USDT", "bitget": "SOLUSDT",
            "aster": "SOLUSDT", "kraken": "PF_SOLUSD", "lighter": "SOL"},
}


def align(a, start, end, log):
    """Выравнивание времени свечи Gate с другими биржами по BTC/ETH/SOL: (1) Пирсон минутных лог-доходностей на лагах −3…+3 (лаг k: ряд Gate
    сдвинут на k минут; пик обязан быть в 0); (2) «общие экстремумы»: K самых больших |доходность минуты| на биржах-эталонах и смещение (в минутах)
    максимума |доходности| Gate в окне ±3 минуты."""
    import math
    nb = (end - start) // MIN_MS
    root = a.ref_root or a.out

    def closes(ex, sym):
        if ex == "gate":
            path = os.path.join(a.out, EX, "candles", safe_name(sym) + ".csv.gz")
        else:
            path = os.path.join(root, ex, "candles", sym + ".csv.gz")
        c = [None] * nb
        with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
            next(f)
            for line in f:
                z = line.split(",")
                t = int(z[0])
                if start <= t < end and float(z[5]) != 0.0:                    # строки с v=0 не считаем свечой (как NaN в панелях)
                    c[(t - start) // MIN_MS] = float(z[4])
        return c

    def rets(c):
        r = [None] * nb
        for i in range(1, nb):
            if c[i] is not None and c[i - 1] is not None and c[i] > 0 and c[i - 1] > 0:
                r[i] = math.log(c[i] / c[i - 1])
        return r

    def corr(ra, rb, k):
        xs, ys = [], []
        for i in range(nb):
            j = i + k
            if 0 <= j < nb and ra[i] is not None and rb[j] is not None:
                xs.append(ra[i])
                ys.append(rb[j])
        n = len(xs)
        if n < 30:
            return None, n
        mx, my = sum(xs) / n, sum(ys) / n
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        sxx = sum((x - mx) ** 2 for x in xs)
        syy = sum((y - my) ** 2 for y in ys)
        return (sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None), n

    exs = [e for e in ("binance", "bybit", "okx", "bingx", "bitget", "aster", "kraken", "lighter")
           if os.path.isdir(os.path.join(root, e, "candles"))]
    ref = "binance"
    offs = collections.defaultdict(collections.Counter)          # кто -> смещение -> число событий
    peak_bad = pairs = 0
    for coin, names in ALIGN_NAMES.items():
        rg = rets(closes("gate", names["gate"]))
        log("align %s: Gate %s: доходностей %d" % (coin, names["gate"], sum(1 for x in rg if x is not None)))
        rr = {}
        for ex in exs:
            try:
                rr[ex] = rets(closes(ex, names[ex]))
            except OSError:
                continue
        for ex, re_ in rr.items():
            cs = {k: corr(re_, rg, k) for k in range(-3, 4)}                  # corr(r_ex[t], r_gate[t+k]): k>0 — Gate «позже»
            best = max(cs, key=lambda k: (cs[k][0] if cs[k][0] is not None else -2))
            pairs += 1
            peak_bad += (best != 0)
            log("align %s Gate~%-8s n=%d  лаги −3…+3: %s  пик k=%d" % (
                coin, ex, cs[0][1], " ".join("%.4f" % (cs[k][0] if cs[k][0] is not None else float("nan")) for k in range(-3, 4)), best))
        if ref in rr:                                            # экстремумы эталона и смещение максимума |доходности| у остальных (окно ±3 минуты)
            top = sorted((i for i in range(nb) if rr[ref][i] is not None), key=lambda i: -abs(rr[ref][i]))[:a.align_k]
            for who, series in [("gate", rg)] + [(e, rr[e]) for e in ("bybit", "okx", "bitget", "bingx") if e in rr]:
                for i in top:
                    cand = [(abs(series[j]), j - i) for j in range(max(0, i - 3), min(nb, i + 4)) if series[j] is not None]
                    if cand:
                        offs[who][max(cand)[1]] += 1
    log("align: пиков корреляции не в лаге 0: %d из %d пар" % (peak_bad, pairs))
    for who, c in offs.items():
        tot = sum(c.values())
        log("align: экстремумы %s (%d самых больших |доходностей| минуты на BTC/ETH/SOL, всего %d событий): смещение максимума у %-7s в окне ±3 мин: %s, ровно 0: %.1f%%" % (
            ref, a.align_k, tot, who, dict(sorted(c.items())), 100.0 * c[0] / max(tot, 1)))


def trades_check(a, start, end, log):
    """Независимая сверка записанных свечей с ТИКОВЫМ архивом сделок Gate (futures_usdt/trades/<ГГГГММ>/<КОНТРАКТ>-<ГГГГММ>.csv.gz: ts,id,price,size):
    минута пересобирается из сделок (high/low = экстремумы цен, close = цена сделки с максимальным id, v = Σ|size|×quanto) и сравнивается с файлом
    свечей ТОЧНО (Decimal) за все месяцы окна, у которых есть архив. Дополнительно — как у Gate связаны open и close: доля минут, где open = close
    предыдущей минуты (свеча «непрерывная») и где open = цена первой сделки минуты. Контракты — --symbols (имена как в instruments.csv)."""
    rows = {r["native_id"]: r for r in load_inst(os.path.join(a.out, EX, "instruments.csv"))}
    cl = Client(a.rate, a.arch_rate, log)
    months = month_list(start, end)
    names = [s.strip() for s in a.symbols.split(",") if s.strip()]
    tot = collections.Counter()
    for name in names:
        r = rows.get(name)
        if r is None:
            log("trades: %s нет в instruments.csv" % name)
            continue
        q = Decimal(str(r["contract_size"] or "1"))
        mine = read_candles(os.path.join(a.out, EX, "candles", r["symbol"] + ".csv.gz"))
        c = collections.Counter()
        for ym, m0, m1 in months:
            lo, hi = max(start, m0), min(end, m1)
            data = get_archive(cl, a.out, "trades", ym, name)
            if data is None:
                log("trades: %s %s — архива сделок нет" % (name, ym))
                continue
            acc = {}                                     # минута -> [hi_f, hi_s, lo_f, lo_s, id_max, c_s, id_min, o_s, vsum, n]
            with gzip.open(io.BytesIO(data), "rt", encoding="ascii", newline="") as f:
                for line in f:
                    p = line.split(",")
                    if len(p) < 4:
                        continue
                    m = int(float(p[0])) // 60
                    if not (lo <= m * 60000 < hi):
                        continue
                    tid, px, pf, sz = int(p[1]), p[2], float(p[2]), abs(Decimal(p[3]))
                    z = acc.get(m)
                    if z is None:
                        acc[m] = [pf, px, pf, px, tid, px, tid, px, sz, 1]
                        continue
                    if pf > z[0]:
                        z[0], z[1] = pf, px
                    if pf < z[2]:
                        z[2], z[3] = pf, px
                    if tid > z[4]:
                        z[4], z[5] = tid, px
                    if tid < z[6]:
                        z[6], z[7] = tid, px
                    z[8] += sz
                    z[9] += 1
            c["trades"] += sum(z[9] for z in acc.values())
            c["minutes_with_trades"] += len(acc)
            for m, z in acc.items():
                row = mine.get(m * 60000)
                if row is None:
                    c["only_trades"] += 1
                    continue
                c["common"] += 1
                dh, dl, dc, dv = Decimal(row[2]) != Decimal(z[1]), Decimal(row[3]) != Decimal(z[3]), Decimal(row[4]) != Decimal(z[5]), Decimal(row[5]) != z[8] * q
                c["diff_h"] += dh
                c["diff_l"] += dl
                c["diff_c"] += dc
                c["diff_v"] += dv
                c["open_eq_first_trade"] += Decimal(row[1]) == Decimal(z[7])
                if (dh or dl or dc or dv) and c["shown"] < 6:
                    c["shown"] += 1
                    log("  расхождение %s %s: файл h=%s l=%s c=%s v=%s | сделки h=%s l=%s c=%s v=%s (n=%d)" % (
                        name, ms_to_str(m * 60000), row[2], row[3], row[4], row[5], z[1], z[3], z[5], dec_str(z[8] * q), z[9]))
            for t, row in mine.items():
                if lo <= t < hi and Decimal(row[5]) != 0 and (t // 60000) not in acc:
                    c["only_candle_nonzero"] += 1
            for t, row in mine.items():
                pr = mine.get(t - MIN_MS)
                if lo <= t < hi and pr is not None:
                    c["pairs"] += 1
                    c["open_eq_prev_close"] += Decimal(row[1]) == Decimal(pr[4])
        log("trades %s: сделок %d, минут со сделками %d, общих с файлом %d, только в сделках %d, ненулевых минут файла без сделок %d | расхождений h/l/c/v: %d/%d/%d/%d | "
            "open=close предыдущей минуты: %d из %d пар соседних минут; open=цене первой сделки: %d из %d" % (
                name, c["trades"], c["minutes_with_trades"], c["common"], c["only_trades"], c["only_candle_nonzero"], c["diff_h"], c["diff_l"],
                c["diff_c"], c["diff_v"], c["open_eq_prev_close"], c["pairs"], c["open_eq_first_trade"], c["common"]))
        tot.update(c)
    log("trades ИТОГ: контрактов %d, минут сверено %d, расхождений h/l/c/v: %d/%d/%d/%d, только в сделках %d, ненулевых минут файла без сделок %d" % (
        len(names), tot["common"], tot["diff_h"], tot["diff_l"], tot["diff_c"], tot["diff_v"], tot["only_trades"], tot["only_candle_nonzero"]))


# ---------------------------------------------------------------------------------------------------------------- main
def main():
    ap = common_args("Gate USDT-перпетуалы: минутные свечи (архив + REST) и фандинг за окно", default_rate=8.0, default_workers=6)
    ap.add_argument("--arch-rate", type=float, default=10.0, help="запросов/с к архиву/листингу (отдельно от REST)")
    ap.add_argument("--mode", choices=("download", "check", "finalize", "audit", "align", "trades"), default="download")
    ap.add_argument("--ref-root", default="", help="--mode align: корень выгрузок других бирж (<корень>/<биржа>/candles); по умолчанию --out")
    ap.add_argument("--align-k", type=int, default=15, help="--mode align: число самых больших минутных движений эталона на символ")
    ap.add_argument("--check-sample", type=int, default=5, help="контрактов для поминутной сверки с REST (--mode check)")
    ap.add_argument("--check-seed", type=int, default=20261107)
    a = ap.parse_args()
    start, end = parse_ts(a.start), parse_ts(a.end)
    if end <= start:
        sys.exit("end <= start")
    ensure_dirs(a.out, EX)
    log = make_logger(a.out, EX, ("list" if a.list_only else a.mode))
    if a.mode in ("finalize", "audit", "align", "trades"):
        {"finalize": lambda: finalize(a, log), "audit": lambda: audit(a, start, end, log), "align": lambda: align(a, start, end, log),
         "trades": lambda: trades_check(a, start, end, log)}[a.mode]()
        return
    cl = Client(a.rate, a.arch_rate, log)
    months = month_list(start, end)
    log("окно [%s, %s) UTC, rate=%.1f/с (REST), arch-rate=%.1f/с, workers=%d, месяцы %s" % (
        ms_to_str(start), ms_to_str(end), a.rate, a.arch_rate, a.workers, ",".join(m[0] for m in months)))
    rows, lst, excluded = fetch_universe(cl, months, start, end, log)
    rows.sort(key=lambda r: r["symbol"])
    if a.mode == "download":                                   # check/trades/… справочник не перезаписывают (там delisted_ms после finalize)
        write_inst(os.path.join(a.out, EX, "instruments.csv"), rows)
    cnt = collections.Counter((r["class"], r["status"]) for r in rows)
    log("instruments.csv: %d контрактов в окне (исключено: %s)" % (len(rows), dict(excluded)))
    for k, v in sorted(cnt.items()):
        log("  класс %-9s статус %-9s %d" % (k[0], k[1], v))
    if a.list_only:
        finalize(a, log)
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
    if a.mode == "check":
        check(a, cl, items, months, lst, start, end, log)
        return
    if not a.force:
        n0 = len(items)
        items = [r for r in items if not is_done(a.out, EX, r["symbol"])]
        log("пропущено готовых: %d" % (n0 - len(items)))
    log("к закачке: %d контрактов" % len(items))
    res = run_parallel(items, lambda it: one(it, a, start, end, cl, months, lst), a.workers, log)
    log("статистика клиента: %s" % dict(cl.stats))
    for s, e in res["fails"]:
        log("СБОЙ %s: %s" % (s, e))
    finalize(a, log)
    sys.exit(1 if res["fail"] else 0)


if __name__ == "__main__":
    main()
