#!/usr/bin/env python3
"""TK-490 (В-94 + слово владельца 04.11 «ещё открытый интерес»): открытый интерес 5 мин USDT-перпетуалов вселенной, 2025-01-01 … 03.11.
usage: tk490-oi.py bybit|binance <OUT> <symbols.txt> [threads] [rps]      OUT/<биржа>/<SYM>.csv.gz, охват — OUT/cov-<биржа>/<SYM>.tsv
  bybit  : REST /v5/market/open-interest intervalTime=5min (самый мелкий шаг истории), 200 точек/запрос, назад от конца по endTime;
           колонки как у TK-415 (читатели G-017): t_ms,open_interest,single_open_interest. Accept-Encoding gzip.
  binance: архив data.binance.vision (S3 Токио, keep-alive) futures/um/daily/metrics (openInterestHist REST даёт ~30 суток); список дней — S3 ListObjectsV2
           (в архиве лежат ровно существующие сутки: дыры видны без 404-запросов), целостность — md5 скачанного = ETag объекта.
           Колонки: t_ms (create_time UTC → мс), open_interest (= sum_open_interest), open_interest_value (= sum_open_interest_value),
           далее остальные колонки архива как есть (count_toptrader_long_short_ratio, sum_toptrader_long_short_ratio,
           count_long_short_ratio, sum_taker_long_short_vol_ratio); symbol опущен (имя файла).
Готовое пропускается; запись <SYM>.csv.gz.part<pid> → os.replace. Env: OI_START/OI_END (мс, END не включая), по умолчанию 2025-01-01 … 2026-11-04 00:00 UTC."""
import csv, gzip, hashlib, http.client, io, json, os, re, sys, threading, time, urllib.error, urllib.parse, urllib.request, zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

START = int(os.environ.get("OI_START", 1735689600000))  # 2025-01-01 00:00 UTC
END = int(os.environ.get("OI_END", 1793750400000))  # 2026-11-04 00:00 UTC (не включая)
UA = {"User-Agent": "alpha-tk490"}
BN_HOST = "s3-ap-northeast-1.amazonaws.com"  # data.binance.vision (CloudFront) с Deck зависает; S3 Токио — 100+ файлов/с на 64 потоках с keep-alive (замер 04.11)
BN_PFX = "/data.binance.vision/"
tl = threading.local()
lock = threading.Lock()
state = {"next": 0.0, "pause": 0.0, "reqs": 0, "bytes": 0, "e429": 0}


def throttle(rps):
    while True:
        with lock:
            now = time.monotonic()
            wait = max(state["next"] - now, state["pause"] - now)
            if wait <= 0:
                state["next"] = now + 1.0 / rps
                return
        time.sleep(min(wait, 1.0))


def get(url, rps=None, gz=False, retries=12):
    """Тело ответа; 404 → None; 403/429 → общая пауза и повтор."""
    hdr = dict(UA)
    if gz:
        hdr["Accept-Encoding"] = "gzip"
    for attempt in range(retries):
        if rps:
            throttle(rps)
        try:
            with lock:
                state["reqs"] += 1
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=30) as r:
                raw = r.read()
                with lock:
                    state["bytes"] += len(raw)
                return gzip.decompress(raw) if r.headers.get("Content-Encoding") == "gzip" else raw
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (403, 429, 418):
                with lock:
                    state["e429"] += 1
                    state["pause"] = max(state["pause"], time.monotonic() + 15 * (attempt + 1))
                print("HTTP %d, пауза %ds %s" % (e.code, 15 * (attempt + 1), url[:80]), file=sys.stderr, flush=True)
                continue
            time.sleep(2 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("не вышло: " + url)


def s3get(path):
    """GET по постоянному соединению потока (keep-alive: без TLS на каждый файл); 404 → None."""
    for attempt in range(10):
        c = getattr(tl, "c", None)
        if c is None:
            c = tl.c = http.client.HTTPSConnection(BN_HOST, timeout=20)
        try:
            with lock:
                state["reqs"] += 1
            c.request("GET", path, headers=UA)
            r = c.getresponse()
            b = r.read()
            if r.status == 200:
                with lock:
                    state["bytes"] += len(b)
                return b
            if r.status == 404:
                return None
            if r.status in (403, 429, 503):
                with lock:
                    state["e429"] += 1
            tl.c = None
            time.sleep(2 * (attempt + 1))
        except (OSError, http.client.HTTPException):
            tl.c = None
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("не вышло: " + path)


def save(out, ex, sym, header, rows, nreq, note):
    os.makedirs("%s/%s" % (out, ex), exist_ok=True)
    os.makedirs("%s/cov-%s" % (out, ex), exist_ok=True)
    dst = "%s/%s/%s.csv.gz" % (out, ex, sym)
    with gzip.open(dst + ".part%d" % os.getpid(), "wt", encoding="ascii", compresslevel=6, newline="") as g:
        g.write(",".join(header) + "\n")
        for r in rows:
            g.write(",".join(str(x) for x in r) + "\n")
    os.replace(dst + ".part%d" % os.getpid(), dst)
    with open("%s/cov-%s/%s.tsv" % (out, ex, sym), "w", encoding="utf-8") as c:
        c.write("%s\t%d\t%s\t%s\t%d\t%s\n" % (sym, len(rows), rows[0][0] if rows else "", rows[-1][0] if rows else "", nreq, note))


def bybit_one(sym, out, rps):
    if os.path.exists("%s/bybit/%s.csv.gz" % (out, sym)):
        return
    rows, end, n, note = {}, END - 1, 0, ""
    while end >= START:
        u = "https://api.bybit.com/v5/market/open-interest?category=linear&symbol=%s&intervalTime=5min&startTime=%d&endTime=%d&limit=200" % (sym, START, end)
        for attempt in range(12):
            body = get(u, rps, gz=True)
            d = json.loads(body) if body else {"retCode": -1, "retMsg": "404"}
            if d.get("retCode") in (10006, 10016):  # лимит / сбой сервиса
                with lock:
                    state["pause"] = max(state["pause"], time.monotonic() + 5 * (attempt + 1))
                continue
            break
        n += 1
        if d.get("retCode") != 0:
            note = "retCode %s %s" % (d.get("retCode"), d.get("retMsg"))
            break
        lst = d["result"]["list"]
        if not lst:
            break
        for k in lst:
            t = int(k["timestamp"])
            if START <= t < END:
                rows[t] = (t, k["openInterest"], k.get("singleOpenInterest", ""))
        oldest = min(int(k["timestamp"]) for k in lst)
        if oldest >= end:
            break
        end = oldest - 1
    save(out, "bybit", sym, ["t_ms", "open_interest", "single_open_interest"], [rows[t] for t in sorted(rows)], n, note)


def bn_list(sym):
    """[(день 'YYYY-MM-DD', ETag)] метрик символа в окне из S3-списка (2 ключа на день: zip и .CHECKSUM)."""
    days, token = [], None
    d0 = datetime.fromtimestamp(START / 1000, timezone.utc).strftime("%Y-%m-%d")
    d1 = datetime.fromtimestamp((END - 1) / 1000, timezone.utc).strftime("%Y-%m-%d")
    while True:
        u = BN_PFX + "?list-type=2&prefix=data/futures/um/daily/metrics/%s/&max-keys=1000" % sym
        if token:
            u += "&continuation-token=" + urllib.parse.quote(token, safe="")
        body = s3get(u).decode()
        for m in re.finditer(r"<Contents><Key>[^<]*-metrics-(\d{4}-\d\d-\d\d)\.zip</Key>.*?<ETag>&quot;([0-9a-f]+)&quot;</ETag>", body):
            if d0 <= m.group(1) <= d1:
                days.append((m.group(1), m.group(2)))
        mt = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", body)
        if not mt:
            return sorted(set(days))
        token = mt.group(1)


def bn_day(sym, day, etag):
    raw = s3get(BN_PFX + "data/futures/um/daily/metrics/%s/%s-metrics-%s.zip" % (sym, sym, day))
    if raw is None:
        raise RuntimeError("404 %s %s" % (sym, day))
    if "-" not in etag and hashlib.md5(raw).hexdigest() != etag:
        raise RuntimeError("md5 %s %s" % (sym, day))
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        return z.read(z.namelist()[0]).decode()


def binance_one(sym, out, pool):
    if os.path.exists("%s/binance/%s.csv.gz" % (out, sym)):
        return
    days = bn_list(sym)
    texts = list(pool.map(lambda de: bn_day(sym, de[0], de[1]), days))
    rows, header = {}, None
    for txt in texts:
        rd = csv.reader(io.StringIO(txt))
        h = next(rd)
        if header is None:
            header = h
        elif h != header:
            raise RuntimeError("заголовок %s" % sym)
        ci = h.index("create_time")
        keep = [i for i, c in enumerate(h) if c not in ("create_time", "symbol")]
        for r in rd:
            if not r:
                continue
            t = int(datetime.strptime(r[ci], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)
            rows[t] = (t,) + tuple(r[i] for i in keep)
    names = {"sum_open_interest": "open_interest", "sum_open_interest_value": "open_interest_value"}
    hdr = ["t_ms"] + [names.get(h[i], h[i]) for i in range(len(h)) if h[i] not in ("create_time", "symbol")] if header else ["t_ms"]
    save(out, "binance", sym, hdr, [rows[t] for t in sorted(rows)], len(days) + 1, "дней в архиве %d" % len(days))


def main():
    ex, out, syms = sys.argv[1], sys.argv[2], [s.strip() for s in open(sys.argv[3]) if s.strip()]
    threads = int(sys.argv[4]) if len(sys.argv) > 4 else (64 if ex == "bybit" else 24)
    rps = float(sys.argv[5]) if len(sys.argv) > 5 else 50.0
    print(ex, "символов", len(syms), "окно", START, END, flush=True)
    t0, fails = time.time(), []
    if ex == "bybit":
        fn = lambda s: bybit_one(s, out, rps)
    else:  # threads — потоков символов (ждут), суточные файлы качает общий пул из OI_DAYTHREADS (128) потоков с keep-alive
        daypool = ThreadPoolExecutor(int(os.environ.get("OI_DAYTHREADS", 128)))
        fn = lambda s: binance_one(s, out, daypool)
    with ThreadPoolExecutor(threads) as pool:
        futs = {pool.submit(fn, s): s for s in syms}
        for i, fu in enumerate(futs, 1):
            try:
                fu.result()
            except Exception as e:  # noqa: BLE001
                fails.append((futs[fu], str(e)))
            if i % 10 == 0:
                print("%d/%d, %.0f с, запросов %d, МБ %.0f, 429: %d" % (i, len(syms), time.time() - t0, state["reqs"], state["bytes"] / 1e6, state["e429"]), flush=True)
    print("готово: %d/%d, провалов %d, запросов %d, МБ %.0f, %.0f с" % (len(syms) - len(fails), len(syms), len(fails), state["reqs"], state["bytes"] / 1e6, time.time() - t0), flush=True)
    for f_ in fails:
        print("FAIL", f_, flush=True)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
