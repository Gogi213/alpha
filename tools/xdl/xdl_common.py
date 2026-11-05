"""Общий модуль закачки публичных минутных свечей и фандинга бирж (стат-арбитраж «на всём», TK-стат-арб, 05.11.2026).

Только стандартная библиотека (python 3.11+: работает на VPS София 3.12 и на Steam Deck 3.14). Секретов нет — только публичные данные.
Формат результата (общий для всех бирж):
  <out>/<exchange>/candles/<SYMBOL>.csv.gz   — заголовок `t_open_ms,o,h,l,c,v,quote_v` (1 минута; на Hyperliquid — `candles15/` по 15 минут),
                                              строки по возрастанию t_open_ms, без дублей, t_open_ms кратно интервалу; ДЫРЫ НЕ ДОРИСОВЫВАЮТСЯ
                                              (нет свечи — нет строки); v — объём в базовых единицах (контракты → в базовые через contract_size),
                                              quote_v — оборот в котируемой валюте (если биржа не отдаёт — пусто, не придумывать).
  <out>/<exchange>/funding/<SYMBOL>.csv.gz   — заголовок `time_ms,rate,interval_h` (ставки как отдаёт биржа; interval_h — интервал выплат, если известен).
  <out>/<exchange>/instruments.csv           — по строке на контракт (поля INSTR_FIELDS), ВСЕ активные и снятые за окно перпетуалы/свопы.
  <out>/<exchange>/_state/<SYMBOL>.done      — метка «символ докачан за окно» (повтор запуска пропускает готовое).
  <out>/<exchange>/_logs/…                   — журналы.
Окно: [start_ms, end_ms) в UTC, по умолчанию 2026-10-01 00:00 … 2026-11-05 00:00 (целые сутки).
"""
import csv, gzip, io, json, os, sys, time, threading, argparse, datetime, urllib.request, urllib.parse, urllib.error, random

INSTR_FIELDS = ["exchange", "symbol", "native_id", "base", "quote", "settle", "class", "class_src", "status", "contract_size",
                "tick_size", "listed_ms", "delisted_ms", "funding_interval_h", "taker_fee_bps", "maker_fee_bps", "note"]
CLASSES = ("crypto", "equity", "etf", "index", "metal", "energy", "commodity", "fx", "other")
START_DEFAULT = int(datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
END_DEFAULT = int(datetime.datetime(2026, 11, 5, tzinfo=datetime.timezone.utc).timestamp() * 1000)
MIN_MS = 60_000


class RateLimiter:
    """Простой общий ограничитель: не больше `rate` запросов в секунду на процесс (потокобезопасный)."""
    def __init__(self, rate):
        self.dt = 1.0 / max(rate, 0.01)
        self.lock = threading.Lock()
        self.next = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next)
            self.next = t + self.dt
        if t > now:
            time.sleep(t - now)


class Http:
    def __init__(self, rate=5.0, retries=6, timeout=25, ua="alpha-xdl/1"):
        self.rl = RateLimiter(rate)
        self.retries, self.timeout, self.ua = retries, timeout, ua
        self.stats = {"req": 0, "retry": 0, "http_err": {}}
        self.lock = threading.Lock()

    def get_json(self, url, params=None, headers=None, data=None, method=None):
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        h = {"User-Agent": self.ua, "Accept": "application/json"}
        if headers:
            h.update(headers)
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            h.setdefault("Content-Type", "application/json")
        last = None
        for attempt in range(self.retries):
            self.rl.wait()
            try:
                req = urllib.request.Request(url, data=body, headers=h, method=method)
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = r.read()
                with self.lock:
                    self.stats["req"] += 1
                return json.loads(raw)
            except urllib.error.HTTPError as e:
                last = e
                with self.lock:
                    self.stats["http_err"][e.code] = self.stats["http_err"].get(e.code, 0) + 1
                if e.code in (400, 404):          # запрос неверен/нет данных — повтор не поможет
                    raise
                sleep = (2 ** attempt) * 0.5 + random.random()
                if e.code in (418, 429):
                    sleep = max(sleep, 5 * (attempt + 1))
                time.sleep(min(sleep, 60))
            except Exception as e:                # сеть/таймаут/разбор
                last = e
                time.sleep(min((2 ** attempt) * 0.5 + random.random(), 30))
            with self.lock:
                self.stats["retry"] += 1
        raise RuntimeError("запрос не удался после %d попыток: %s | %s" % (self.retries, url[:160], last))


def ms_to_str(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")


def ensure_dirs(out, ex, intervals=("candles",)):
    for d in list(intervals) + ["funding", "_state", "_logs"]:
        os.makedirs(os.path.join(out, ex, d), exist_ok=True)


def write_candles(path, rows, step_ms=MIN_MS, start_ms=None, end_ms=None):
    """rows: iterable (t_open_ms, o, h, l, c, v, quote_v|None). Сортировка, снятие дублей (последнее значение), фильтр окна,
    проверка кратности шагу. Возвращает (число строк, число дыр в минутах между первой и последней)."""
    d = {}
    for r in rows:
        t = int(r[0])
        if t % step_ms:
            raise ValueError("t_open_ms %d не кратно %d" % (t, step_ms))
        if (start_ms is not None and t < start_ms) or (end_ms is not None and t >= end_ms):
            continue
        d[t] = r
    ts = sorted(d)
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_open_ms", "o", "h", "l", "c", "v", "quote_v"])
        for t in ts:
            r = d[t]
            w.writerow([t] + [("" if x is None else x) for x in r[1:7]])
    os.replace(tmp, path)
    gaps = ((ts[-1] - ts[0]) // step_ms + 1 - len(ts)) if ts else 0
    return len(ts), gaps


def write_funding(path, rows):
    d = {}
    for r in rows:
        d[int(r[0])] = r
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_ms", "rate", "interval_h"])
        for t in sorted(d):
            r = d[t]
            w.writerow([t, r[1], r[2] if len(r) > 2 and r[2] is not None else ""])
    os.replace(tmp, path)
    return len(d)


def write_instruments(out, ex, instruments):
    """instruments: список dict с ключами INSTR_FIELDS (недостающие — пусто)."""
    p = os.path.join(out, ex, "instruments.csv")
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=INSTR_FIELDS)
        w.writeheader()
        for i in instruments:
            bad = i.get("class", "other")
            if bad not in CLASSES:
                raise ValueError("неизвестный класс %r у %s" % (bad, i.get("symbol")))
            w.writerow({k: i.get(k, "") for k in INSTR_FIELDS})
    return p


def is_done(out, ex, sym):
    return os.path.exists(os.path.join(out, ex, "_state", sym + ".done"))


def mark_done(out, ex, sym, info=""):
    with open(os.path.join(out, ex, "_state", sym + ".done"), "w") as f:
        f.write(info)


def safe_name(sym):
    """Имя файла: символ как есть, но без путевых символов (':' '/' '\\' пробел)."""
    return "".join(c if c.isalnum() or c in "-_.@" else "_" for c in sym)


def common_args(description, default_rate=5.0, default_workers=4):
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--out", required=True, help="корень результата (создаётся <out>/<exchange>/…)")
    ap.add_argument("--start", default=ms_to_str(START_DEFAULT), help="начало окна UTC 'YYYY-MM-DD[ HH:MM]' (включительно)")
    ap.add_argument("--end", default=ms_to_str(END_DEFAULT), help="конец окна UTC (исключительно)")
    ap.add_argument("--rate", type=float, default=default_rate, help="запросов/с на процесс")
    ap.add_argument("--workers", type=int, default=default_workers)
    ap.add_argument("--symbols", default="", help="через запятую: только эти символы (в формате биржи); пусто — все")
    ap.add_argument("--limit-symbols", type=int, default=0, help="для проб: первые N символов после фильтра")
    ap.add_argument("--list-only", action="store_true", help="только список контрактов -> instruments.csv, без свечей")
    ap.add_argument("--force", action="store_true", help="игнорировать метки .done")
    return ap


def parse_ts(s):
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(datetime.datetime.strptime(s, fmt).replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
        except ValueError:
            pass
    raise ValueError("не разобрано время: " + s)


def run_parallel(items, fn, workers, log):
    """items: список; fn(item) -> строка-статус; исключения не роняют прогон (пишутся в журнал и итог)."""
    lock = threading.Lock()
    idx = [0]
    res = {"ok": 0, "fail": 0, "fails": []}
    t0 = time.time()

    def worker():
        while True:
            with lock:
                i = idx[0]
                idx[0] += 1
            if i >= len(items):
                return
            it = items[i]
            try:
                st = fn(it)
                with lock:
                    res["ok"] += 1
                log("[%d/%d] %s %s" % (i + 1, len(items), it if isinstance(it, str) else it.get("symbol", it), st))
            except Exception as e:
                with lock:
                    res["fail"] += 1
                    res["fails"].append((str(it)[:60], str(e)[:200]))
                log("[%d/%d] СБОЙ %s: %s" % (i + 1, len(items), str(it)[:60], str(e)[:300]))

    ths = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, workers))]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    log("итого ok=%d сбоев=%d за %.0f с" % (res["ok"], res["fail"], time.time() - t0))
    return res


def make_logger(out, ex, name="run"):
    p = os.path.join(out, ex, "_logs", "%s-%s.log" % (name, datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S")))
    f = open(p, "a", encoding="utf-8")
    lock = threading.Lock()

    def log(msg):
        line = "%s %s" % (datetime.datetime.utcnow().strftime("%H:%M:%S"), msg)
        with lock:
            f.write(line + "\n")
            f.flush()
            print(line, flush=True)
    return log
