#!/usr/bin/env python3
"""1m klines of Binance USD-M and Bybit linear USDT perpetuals, stdlib only.

Steps (resumable; every file is written atomically, a finished symbol gets <SYMBOL>/_done.json):
  symbols  candidate list per exchange (exchangeInfo / instruments-info, incl. contracts delisted inside the window)
  daily    1d klines of every candidate -> daily_turnover.csv + filter.json (>= threshold on at least one full UTC day)
  overlap  Bybit qualifying symbols whose base asset is a Binance USD-M perpetual are not downloaded from Bybit
  fetch    1m of the qualifying symbols for the full window -> <root>/<exchange>/<SYMBOL>/<YYYY-MM-DD>.csv
  partial  the day after the window, up to the last closed minute -> <YYYY-MM-DD>.partial.csv
  verify   rows/gaps per symbol-day, 1d vs sum(1m), random REST spot-check -> <root>/<exchange>/manifest.json
Groups: prep = symbols+daily+overlap, download = fetch+partial+verify, all = both. Launch under systemd-run: launch.sh.
Warmup (same symbols as the main run, earlier window, state files get a tag): warmup --start 2026-09-01 --days 14 --tag .warmup
  = daily+fetch+verify -> same <SYMBOL>/<day>.csv layout, daily.warmup.json, filter.warmup.json, manifest.warmup.json.
funding  funding-rate history of the same symbols from --funding-from to now -> <SYMBOL>/funding.csv + funding_manifest.json
  (Binance /fapi/v1/fundingRate, paced to 48 % of its 500/5min/IP limit; Bybit /v5/market/funding/history).

Values are kept exactly as the exchange sends them (strings), nothing is filled or rounded.
Binance 1m: data.binance.vision zips (sha256 against .CHECKSUM), monthly where published, else daily, REST only for
days not published; Binance REST weight is held under 50 % of 2400/min using x-mbx-used-weight-1m (IP-wide).
Bybit 1m: /v5/market/kline, 1000 per request, rate capped far below the per-IP limit.
"""
import argparse
import datetime as dt
import hashlib
import http.client
import io
import json
import os
import random
import re
import statistics
import sys
import threading
import time
import urllib.parse
import zipfile
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

MIN = 60_000
DAY = 86_400_000
HEADER = {
    "binance": "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore",
    "bybit": "open_time,open,high,low,close,volume,turnover",
}
NFIELDS = {"binance": 12, "bybit": 7}
S3 = "s3-ap-northeast-1.amazonaws.com"
_log_lock = threading.Lock()


def log(msg):
    with _log_lock:
        print(f"{time.strftime('%H:%M:%S', time.gmtime())} {msg}", flush=True)


def day_ms(s):
    return int(dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def day_str(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dec(x):
    return format(x, "f")


class Cfg:
    def __init__(self, a):
        self.root = a.root
        self.start = day_ms(a.start)
        self.days = a.days
        self.end = self.start + a.days * DAY          # exclusive end of the last full day
        self.day_list = [day_str(self.start + i * DAY) for i in range(a.days)]
        self.thr = a.threshold
        self.workers = a.workers
        self.only = set(filter(None, a.only.split(",")))
        self.force = a.force
        self.tag = a.tag
        self.funding_from = a.funding_from
        self.progress_lock = threading.Lock()

    def name(self, base):
        """State file name of this run: daily.json -> daily.warmup.json when --tag .warmup."""
        stem, ext = os.path.splitext(base)
        return stem + self.tag + ext

    def path(self, ex, *p):
        return os.path.join(self.root, ex, *p)

    def jload(self, ex, name, default=None):
        p = self.path(ex, name)
        if not os.path.exists(p):
            return default
        with open(p) as f:
            return json.load(f)

    def jsave(self, ex, name, obj):
        write_atomic(self.path(ex, name), json.dumps(obj, indent=1, sort_keys=True) + "\n")

    def progress(self, ex, step, done, total):
        p = os.path.join(self.root, "progress.json")
        with self.progress_lock:
            cur = json.load(open(p)) if os.path.exists(p) else {}
            cur.setdefault(ex, {})[step] = {"done": done, "total": total, "pct": round(100 * done / max(total, 1), 1), "t": iso(int(time.time() * 1000))}
            write_atomic(p, json.dumps(cur, indent=1) + "\n")


def write_atomic(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}.{threading.get_ident()}"
    with open(tmp, "w", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


class ApiError(Exception):
    pass


class Http:
    def __init__(self, host):
        self.host = host
        self.local = threading.local()

    def get(self, path):
        path = urllib.parse.quote(path, safe="/?&=%:.-_~")      # symbols can be non-ASCII; '%' kept so encoded cursors stay as is
        last = None
        for attempt in range(6):
            try:
                c = getattr(self.local, "c", None)
                if c is None:
                    c = self.local.c = http.client.HTTPSConnection(self.host, timeout=40)
                c.request("GET", path)
                r = c.getresponse()
                body = r.read()
                return r.status, {k.lower(): v for k, v in r.getheaders()}, body
            except (OSError, http.client.HTTPException) as e:
                last = e
                self.local.c = None
                time.sleep(min(2 ** attempt, 30))
        raise ApiError(f"GET {self.host}{path}: {last}")


class Binance:
    """fapi REST under one lock; weight capped at 50 % of the documented 2400/min, IP-wide via the response header."""

    def __init__(self):
        self.http = Http("fapi.binance.com")
        self.lock = threading.Lock()
        self.cap = 1200
        self.minute = -1
        self.used = 0
        self.bans = 0
        self.last_spaced = 0.0

    def get(self, path, cost, spacing=0.0):
        with self.lock:
            while True:
                m = int(time.time() // 60)
                if m != self.minute:
                    self.minute, self.used = m, 0
                if self.used + cost > self.cap:
                    time.sleep(60 - time.time() % 60 + 0.5)
                    continue
                if spacing:                     # endpoints with their own limit (fundingRate: 500/5min/IP, no usage header)
                    time.sleep(max(0.0, self.last_spaced + spacing - time.time()))
                    self.last_spaced = time.time()
                st, h, body = self.http.get(path)
                self.used += cost
                if "x-mbx-used-weight-1m" in h and int(time.time() // 60) == self.minute:
                    self.used = max(self.used, int(h["x-mbx-used-weight-1m"]))
                if st in (418, 429):
                    ra = int(h.get("retry-after", 60))
                    self.cap = max(self.cap // 2, 100)
                    if st == 418:
                        self.bans += 1
                        if self.bans >= 2:
                            raise ApiError("binance 418 twice, abort")
                    log(f"binance {st}: pause {ra}s, cap -> {self.cap}")
                    time.sleep(ra + 1)
                    continue
                if st != 200:
                    raise ApiError(f"binance {st} {path}: {body[:200]!r}")
                return json.loads(body)


class Bybit:
    """v5 REST: global rate cap (default 20 req/s = 17 % of the 600/5s per-IP limit), pause + halve on 403/429/10006/10018."""

    def __init__(self, rps=20.0):
        self.http = Http("api.bybit.com")
        self.lock = threading.Lock()
        self.rps = rps
        self.next_t = 0.0

    def _pause(self, secs, why):
        with self.lock:
            self.rps = max(self.rps / 2, 2.0)
            self.next_t = max(self.next_t, time.monotonic() + secs)
        log(f"bybit {why}: pause {secs}s, rps -> {self.rps}")

    def get(self, path):
        for _ in range(12):
            with self.lock:
                now = time.monotonic()
                t = max(now, self.next_t)
                self.next_t = t + 1.0 / self.rps
            if t > now:
                time.sleep(t - now)
            st, h, body = self.http.get(path)
            if st in (403, 429):
                self._pause(600 if st == 403 else 30, f"http {st}")
                continue
            if st != 200:
                time.sleep(2)
                continue
            d = json.loads(body)
            if d["retCode"] in (10006, 10018):
                self._pause(10, f"retCode {d['retCode']}")
                continue
            if d["retCode"] in (10000, 10016):              # transient server error
                time.sleep(2)
                continue
            if d["retCode"] != 0:
                raise ApiError(f"bybit {path}: {d['retCode']} {d['retMsg']}")
            return d["result"]
        raise ApiError(f"bybit gave up: {path}")


BN = Binance()
BB = Bybit()
VISION = Http("data.binance.vision")
S3H = Http(S3)


def selected(cfg, ex):
    """Symbols of the main run to download: Binance qualifying, Bybit overlap.json bybit_download."""
    if ex == "bybit":
        syms = cfg.jload(ex, "overlap.json")["bybit_download"]
    else:
        syms = sorted(s for s, f in cfg.jload(ex, "filter.json").items() if f["qualifies"])
    return [s for s in syms if not cfg.only or s in cfg.only]


# ---------------------------------------------------------------- symbols

def s3_list(prefix, marker="", delimiter=True, max_keys=1000):
    out_p, out_k = [], []
    while True:
        q = f"/data.binance.vision?prefix={urllib.parse.quote(prefix)}&max-keys={max_keys}"
        if delimiter:
            q += "&delimiter=/"
        if marker:
            q += f"&marker={urllib.parse.quote(marker)}"
        st, _, body = S3H.get(q)
        if st != 200:
            raise ApiError(f"s3 {st} {q}")
        x = body.decode()
        out_p += re.findall(r"<CommonPrefixes><Prefix>([^<]+)</Prefix>", x)
        out_k += re.findall(r"<Contents><Key>([^<]+)</Key>", x)
        nm = re.search(r"<NextMarker>([^<]+)</NextMarker>", x)
        if "<IsTruncated>true" in x and nm and delimiter:
            marker = nm.group(1)
        elif "<IsTruncated>true" in x and out_k and not delimiter:
            marker = out_k[-1]
        else:
            return out_p, out_k


def symbols_binance(cfg):
    ei = BN.get("/fapi/v1/exchangeInfo", 1)
    usdt = [s for s in ei["symbols"] if s["quoteAsset"] == "USDT" and s["contractType"] in ("PERPETUAL", "TRADIFI_PERPETUAL")]
    cand = [dict(symbol=s["symbol"], base=s["baseAsset"], status=s["status"], contract_type=s["contractType"],
                 onboard_ms=s["onboardDate"], delivery_ms=s["deliveryDate"])
            for s in usdt if s["deliveryDate"] >= cfg.start]
    # symbols the bulk archive knows but exchangeInfo does not: must have no files inside the window
    known = {s["symbol"] for s in ei["symbols"]}
    seen = set()
    for kind in ("monthly", "daily"):
        ps, _ = s3_list(f"data/futures/um/{kind}/klines/")
        seen |= {p.split("/")[-2] for p in ps}
    extra = sorted(s for s in seen if s.endswith("USDT") and "_" not in s and s not in known)
    hits = []
    for s in extra:
        mk = f"data/futures/um/daily/klines/{s}/1d/{s}-1d-{day_str(cfg.start - DAY)}.zip.CHECKSUM"
        _, keys = s3_list(f"data/futures/um/daily/klines/{s}/1d/{s}-1d-", marker=mk, delimiter=False, max_keys=3)
        if any(k.endswith(".zip") for k in keys):
            hits.append(s)
    if hits:
        raise SystemExit(f"bulk archive has symbols missing from exchangeInfo with files in window: {hits}")
    cfg.jsave("binance", "symbols.json", {"listed_usdt_perps_total": len(usdt), "bulk_only_symbols_checked": len(extra), "candidates": cand})
    log(f"binance symbols: listed {len(usdt)}, candidates with window overlap {len(cand)}, bulk-only checked {len(extra)} (none in window)")


def symbols_bybit(cfg):
    items = []
    for status in ("Trading", "Closed", "Settling"):
        cursor = ""
        while True:
            q = f"/v5/market/instruments-info?category=linear&limit=1000&status={status}" + (f"&cursor={cursor}" if cursor else "")   # cursor comes URL-encoded
            try:
                r = BB.get(q)
            except ApiError as e:
                log(f"bybit instruments status={status}: {e}")
                break
            items += r["list"]
            cursor = r.get("nextPageCursor") or ""
            if not cursor:
                break
    seen, cand, total = set(), [], 0
    for s in items:
        if s["contractType"] != "LinearPerpetual" or s["quoteCoin"] != "USDT" or s["symbol"] in seen:
            continue
        seen.add(s["symbol"])
        total += 1
        dlv = int(s["deliveryTime"] or 0)
        if dlv and dlv < cfg.start:
            continue
        cand.append(dict(symbol=s["symbol"], base=s["baseCoin"], status=s["status"], contract_type=s["contractType"],
                         onboard_ms=int(s["launchTime"]), delivery_ms=dlv))
    cfg.jsave("bybit", "symbols.json", {"listed_usdt_perps_total": total, "candidates": cand})
    log(f"bybit symbols: listed {total}, candidates with window overlap {len(cand)}")


# ---------------------------------------------------------------- daily + filter

def bn_daily(sym, cfg):
    try:
        rows = BN.get(f"/fapi/v1/klines?symbol={sym}&interval=1d&startTime={cfg.start}&endTime={cfg.end - 1}&limit=99", 1)
    except ApiError as e:
        if " 400 " in str(e):
            return []
        raise
    return [[str(x) for x in r] for r in rows]


def bb_daily(sym, cfg):
    r = BB.get(f"/v5/market/kline?category=linear&symbol={sym}&interval=D&start={cfg.start}&end={cfg.end - 1}&limit=200")
    return list(reversed(r["list"]))


def daily_step(cfg, ex):
    cand = cfg.jload(ex, "symbols.json")["candidates"]
    if cfg.tag:                                     # warmup: only the symbols the main run downloads
        sel = set(selected(cfg, ex))
        cand = [c for c in cand if c["symbol"] in sel]
    if cfg.only:
        cand = [c for c in cand if c["symbol"] in cfg.only]
    fetch = bn_daily if ex == "binance" else bb_daily
    daily, done = {}, 0
    with ThreadPoolExecutor(1 if ex == "binance" else 3) as pool:
        for c, rows in zip(cand, pool.map(lambda c: fetch(c["symbol"], cfg), cand)):
            daily[c["symbol"]] = {day_str(int(r[0])): r for r in rows if cfg.start <= int(r[0]) < cfg.end}
            done += 1
            if done % 100 == 0:
                cfg.progress(ex, "daily", done, len(cand))
    qi = 7 if ex == "binance" else 6
    flt, lines = {}, ["symbol,day,quote_turnover"]
    for c in cand:
        sym = c["symbol"]
        qv = {d: Decimal(r[qi]) for d, r in daily[sym].items()}
        act = {d: v for d, v in qv.items() if v > 0 and (ex == "bybit" or daily[sym][d][8] != "0")}
        for d in sorted(qv):
            lines.append(f"{sym},{d},{dec(qv[d])}")
        ge = sorted(d for d, v in qv.items() if v >= cfg.thr)
        flt[sym] = dict(days_listed=len(qv), days_active=len(act), days_ge_threshold=len(ge),
                        median_daily_turnover=dec(statistics.median(act.values())) if act else None,
                        max_daily_turnover=dec(max(qv.values())) if qv else None,
                        first_day_ge=ge[0] if ge else None, qualifies=bool(ge))
    cfg.jsave(ex, cfg.name("daily.json"), daily)
    cfg.jsave(ex, cfg.name("filter.json"), flt)
    write_atomic(cfg.path(ex, cfg.name("daily_turnover.csv")), "\n".join(lines) + "\n")
    cfg.progress(ex, "daily", len(cand), len(cand))
    log(f"{ex} daily: {len(cand)} candidates, qualifying {sum(v['qualifies'] for v in flt.values())}")


# ---------------------------------------------------------------- overlap

# Bybit base -> Binance base where the ticker differs for the same asset (confirmed by the daily price ratio in overlap.json)
ALIASES = {"PUMPFUN": "PUMP"}


def norm_base(b):
    b = ALIASES.get(b, b)
    m = re.fullmatch(r"(1M|1(?:0{3,7}))([A-Z0-9].*)", b)
    if m:
        return m.group(2), (10 ** 6 if m.group(1) == "1M" else int(m.group(1)))
    m = re.fullmatch(r"(.+?)(1000)", b)
    if m and not m.group(1).isdigit():
        return m.group(1), 1000
    return b, 1


def overlap_step(cfg):
    bn_c = {c["symbol"]: c for c in cfg.jload("binance", "symbols.json")["candidates"]}
    bb_c = {c["symbol"]: c for c in cfg.jload("bybit", "symbols.json")["candidates"]}
    bn_f, bb_f = cfg.jload("binance", "filter.json"), cfg.jload("bybit", "filter.json")
    bn_d, bb_d = cfg.jload("binance", "daily.json"), cfg.jload("bybit", "daily.json")
    by_norm = {}
    for s, c in bn_c.items():
        by_norm.setdefault(norm_base(c["base"])[0], []).append(s)
    rows, download = [], []
    for sym, f in sorted(bb_f.items()):
        if not f["qualifies"]:
            continue
        base, mult = norm_base(bb_c[sym]["base"])
        hits = by_norm.get(base, [])
        if not hits:
            download.append(sym)
            continue
        bn_sym = hits[0]
        _, bn_mult = norm_base(bn_c[bn_sym]["base"])
        common = sorted(set(bn_d.get(bn_sym, {})) & set(bb_d.get(sym, {})))
        ratio = None
        for d in reversed(common):
            a, b = Decimal(bn_d[bn_sym][d][4]), Decimal(bb_d[sym][d][4])
            if a > 0 and b > 0:
                ratio = float((b / mult) / (a / bn_mult))
                break
        suspect = ratio is None or not (0.8 <= ratio <= 1.25)
        rows.append(dict(bybit=sym, binance=hits, base=base, bybit_base=bb_c[sym]["base"], binance_base=bn_c[bn_sym]["base"],
                         alias=(bb_c[sym]["base"] != bn_c[bn_sym]["base"]), binance_qualifies=bn_f[bn_sym]["qualifies"],
                         underlying_price_ratio=ratio, collision_suspect=suspect))
        if suspect or not bn_f[bn_sym]["qualifies"]:     # no Binance 1m for this asset -> Bybit data is the only copy
            download.append(sym)
    skipped = [r["bybit"] for r in rows if not r["collision_suspect"] and r["binance_qualifies"]]
    out = dict(rule="Bybit symbol skipped if its base asset (multiplier prefix 1000/10000/.../1M or suffix 1000 stripped) equals the base asset of a "
                    "Binance USD-M USDT perpetual that is itself downloaded (qualifies); price ratio outside [0.8,1.25] = suspected ticker collision, "
                    "Binance equivalent below the threshold = no Binance copy -> downloaded from Bybit anyway",
               bybit_qualifying=sum(v["qualifies"] for v in bb_f.values()), overlap=rows, skipped=sorted(skipped),
               bybit_download=sorted(download))
    cfg.jsave("bybit", "overlap.json", out)
    log(f"overlap: bybit qualifying {out['bybit_qualifying']}, skipped (on Binance) {len(skipped)}, to download {len(download)}")


# ---------------------------------------------------------------- fetch

def bulk_zip(sym, kind, name):
    """Lines of a data.binance.vision zip (None on 404); sha256 checked against .CHECKSUM."""
    path = f"/data/futures/um/{kind}/klines/{sym}/1m/{sym}-1m-{name}.zip"
    for attempt in range(3):
        st, _, body = VISION.get(path)
        if st == 404:
            return None
        if st != 200:
            raise ApiError(f"vision {st} {path}")
        st2, _, cs = VISION.get(path + ".CHECKSUM")
        if st2 != 200:
            raise ApiError(f"vision {st2} {path}.CHECKSUM")
        if hashlib.sha256(body).hexdigest() == cs.decode().split()[0]:
            z = zipfile.ZipFile(io.BytesIO(body))
            lines = z.read(z.namelist()[0]).decode().splitlines()
            return [l for l in lines if l and not l.startswith("open_time")]
        log(f"checksum mismatch {path}, retry {attempt + 1}")
    raise ApiError(f"checksum mismatch {path}")


def bn_rest_lines(sym, d0, d1):
    rows = BN.get(f"/fapi/v1/klines?symbol={sym}&interval=1m&startTime={d0}&endTime={d1}&limit=1500", 10)
    return [",".join(str(x) for x in r) for r in rows]


def bb_rest_lines(sym, d0, d1):
    out, t = [], d0
    while t <= d1:
        r = BB.get(f"/v5/market/kline?category=linear&symbol={sym}&interval=1&start={t}&end={min(t + 999 * MIN, d1)}&limit=1000")
        out += [",".join(x) for x in reversed(r["list"])]
        t += 1000 * MIN
    return out


def write_days(cfg, ex, sym, by_day, suffix=".csv"):
    """by_day: day -> list of csv lines; dedupe on open_time, sort; returns (rows, dupes) per written day."""
    res, dupes = {}, 0
    for day, lines in by_day.items():
        uniq = {}
        for l in lines:
            if len(l.split(",")) != NFIELDS[ex]:
                raise ApiError(f"{ex} {sym} {day}: bad row {l[:80]!r}")
            ot = int(l.split(",", 1)[0])
            if ot in uniq:
                dupes += 1
                continue
            uniq[ot] = l
        text = HEADER[ex] + "\n" + "\n".join(uniq[k] for k in sorted(uniq)) + "\n"
        write_atomic(cfg.path(ex, sym, day + suffix), text)
        res[day] = len(uniq)
    return res, dupes


def bucket(lines, cfg):
    by_day = {}
    for l in lines:
        ot = int(l.split(",", 1)[0])
        if cfg.start <= ot < cfg.end:
            by_day.setdefault(day_str(ot), []).append(l)
    return by_day


def bn_fetch_symbol(cfg, sym, drows):
    active = sorted(d for d, r in drows.items() if r[8] != "0")
    months = {}
    for d in active:
        months.setdefault(d[:7], []).append(d)
    today0 = int(time.time() // 86400) * DAY
    src = {"monthly": [], "daily": [], "rest": []}
    got = {}
    for month, days in months.items():
        y, m = int(month[:4]), int(month[5:])
        month_end = day_ms(f"{y + (m == 12)}-{1 if m == 12 else m + 1:02d}-01")
        if month_end <= today0:
            lines = bulk_zip(sym, "monthly", month)
            if lines is not None:
                by_day = bucket(lines, cfg)
                for d, ls in by_day.items():
                    if d[:7] == month:
                        got[d] = ls
                        src["monthly"].append(d)
        for d in days:
            if d in got:
                continue
            lines = bulk_zip(sym, "daily", d)
            if lines is not None:
                got[d] = lines
                src["daily"].append(d)
            else:
                d0 = day_ms(d)
                got[d] = bn_rest_lines(sym, d0, d0 + DAY - 1)
                src["rest"].append(d)
    rows, dupes = write_days(cfg, "binance", sym, got)
    return rows, dupes, src


def bb_fetch_symbol(cfg, sym, drows):
    if not drows:
        return {}, 0, {"rest": []}
    first, last = day_ms(min(drows)), day_ms(max(drows)) + DAY
    lines = bb_rest_lines(sym, first, last - MIN)
    rows, dupes = write_days(cfg, "bybit", sym, bucket(lines, cfg))
    return rows, dupes, {"rest": sorted(rows)}


def fetch_step(cfg, ex):
    daily = cfg.jload(ex, cfg.name("daily.json"))
    syms = selected(cfg, ex)
    todo = [s for s in syms if cfg.force or not os.path.exists(cfg.path(ex, s, cfg.name("_done.json")))]
    log(f"{ex} fetch: {len(syms)} symbols, {len(todo)} to do")
    fn = bn_fetch_symbol if ex == "binance" else bb_fetch_symbol
    done = [len(syms) - len(todo)]

    def one(sym):
        rows, dupes, src = fn(cfg, sym, daily[sym])
        write_atomic(cfg.path(ex, sym, cfg.name("_done.json")), json.dumps(dict(symbol=sym, days=len(rows), rows=sum(rows.values()), dupes=dupes,
                                                                   sources=src, finished=iso(int(time.time() * 1000)))) + "\n")
        done[0] += 1
        if done[0] % 10 == 0 or done[0] == len(syms):
            log(f"{ex} fetch {done[0]}/{len(syms)}")
            cfg.progress(ex, "fetch", done[0], len(syms))

    with ThreadPoolExecutor(cfg.workers if ex == "binance" else 3) as pool:
        for f in [pool.submit(one, s) for s in todo]:
            f.result()
    cfg.progress(ex, "fetch", len(syms), len(syms))
    log(f"{ex} fetch done")


def partial_step(cfg, ex):
    if cfg.tag:
        return
    syms = selected(cfg, ex)
    sc = {c["symbol"]: c for c in cfg.jload(ex, "symbols.json")["candidates"]}
    d0 = cfg.end
    until = min(int(time.time() // 60) * MIN, d0 + DAY)          # exclusive: last closed minute + 1
    suffix = ".partial.csv" if until < d0 + DAY else ".csv"
    res = {}

    def one(sym):
        dl = sc[sym]["delivery_ms"]
        if dl and dl < d0:
            return
        lines = (bn_rest_lines if ex == "binance" else bb_rest_lines)(sym, d0, until - 1)
        if lines:
            rows, dupes = write_days(cfg, ex, sym, {day_str(d0): lines}, suffix)
            res[sym] = dict(rows=rows[day_str(d0)], dupes=dupes)

    with ThreadPoolExecutor(1 if ex == "binance" else 3) as pool:
        for f in [pool.submit(one, s) for s in syms]:
            f.result()
    cfg.jsave(ex, "partial.json", dict(day=day_str(d0), until_ms=until, until_utc=iso(until), file_suffix=suffix, symbols=res))
    log(f"{ex} partial {day_str(d0)} until {iso(until)}: {len(res)} symbols")


# ---------------------------------------------------------------- verify + manifest

def scan_symbol(cfg, ex, sym, drows, meta):
    qi, ci = (7, 8) if ex == "binance" else (6, None)
    d = cfg.path(ex, sym)
    rows_by_day, ots, mism, anomalies, zero_rows = {}, [], [], [], 0
    for day in cfg.day_list:
        p = os.path.join(d, day + ".csv")
        if not os.path.exists(p):
            continue
        with open(p, newline="") as f:
            lines = f.read().split("\n")
        if lines[0] != HEADER[ex] or lines[-1] != "":
            anomalies.append(f"{day}: bad header/trailer")
        body = [l.split(",") for l in lines[1:-1]]
        d0 = day_ms(day)
        prev = None
        vol = Decimal(0)
        qv = Decimal(0)
        cnt = 0
        hi = lo = None
        zero = 0
        for r in body:
            ot = int(r[0])
            if len(r) != NFIELDS[ex] or ot % MIN or not d0 <= ot < d0 + DAY or (prev is not None and ot <= prev):
                anomalies.append(f"{day}: bad row at {ot}")
                break
            prev = ot
            v = Decimal(r[5])
            zero += v == 0
            vol += v
            qv += Decimal(r[qi])
            if ci:
                cnt += int(r[ci])
            h, l = Decimal(r[2]), Decimal(r[3])
            hi = h if hi is None or h > hi else hi
            lo = l if lo is None or l < lo else lo
            ots.append(ot)
        rows_by_day[day] = len(body)
        zero_rows += zero
        dr = drows.get(day)
        if dr:
            for name, a, b in (("volume", vol, Decimal(dr[5])), ("quote", qv, Decimal(dr[qi])), ("high", hi, Decimal(dr[2])), ("low", lo, Decimal(dr[3]))) + \
                    ((("count", cnt, int(dr[ci])),) if ci else ()):
                if a != b:
                    mism.append(dict(day=day, field=name, sum_1m=str(a), kline_1d=str(b)))
        else:
            anomalies.append(f"{day}: 1m file without 1d candle")
    # expected days: Binance — days with trades per 1d; Bybit — every listed day
    expected = sorted(dd for dd, r in drows.items() if ex == "bybit" or r[8] != "0")
    missing_days = [dd for dd in expected if dd not in rows_by_day]
    gaps = []
    for a, b in zip(ots, ots[1:]):
        if b - a > MIN:
            gaps.append([a + MIN, b - MIN, (b - a) // MIN - 1])
    exp_start = max(cfg.start, meta["onboard_ms"] // MIN * MIN)      # nothing is expected before the listing minute
    if ots and expected and ots[0] > exp_start:
        gaps.insert(0, [exp_start, ots[0] - MIN, (ots[0] - exp_start) // MIN])
    alive = not meta["delivery_ms"] or meta["delivery_ms"] >= cfg.end
    if ots and alive and ots[-1] < cfg.end - MIN:
        gaps.append([ots[-1] + MIN, cfg.end - MIN, (cfg.end - ots[-1]) // MIN - 1])
    return dict(rows=sum(rows_by_day.values()), days=len(rows_by_day), rows_by_day_not_1440={k: v for k, v in rows_by_day.items() if v != 1440},
                missing_days=missing_days, zero_volume_rows=zero_rows, gap_minutes=sum(g[2] for g in gaps), gaps=[[iso(a), iso(b), n] for a, b, n in gaps],
                mismatches_1d_vs_1m=mism, anomalies=anomalies)


def rest_day_lines(ex, sym, day):
    d0 = day_ms(day)
    return (bn_rest_lines if ex == "binance" else bb_rest_lines)(sym, d0, d0 + DAY - 1)


def verify_step(cfg, ex):
    meta = {c["symbol"]: c for c in cfg.jload(ex, "symbols.json")["candidates"]}
    flt, daily = cfg.jload(ex, cfg.name("filter.json")), cfg.jload(ex, cfg.name("daily.json"))
    part = {} if cfg.tag else cfg.jload(ex, "partial.json", {})
    done_name = cfg.name("_done.json")
    syms = sorted(s for s in flt if os.path.exists(cfg.path(ex, s, done_name)))
    log(f"{ex} verify: {len(syms)} downloaded symbols")
    out, done = {}, 0
    with ThreadPoolExecutor(2) as pool:
        for s, r in zip(syms, pool.map(lambda s: scan_symbol(cfg, ex, s, daily[s], meta[s]), syms)):
            r["sources"] = json.load(open(cfg.path(ex, s, done_name)))["sources"]
            out[s] = r
            done += 1
            if done % 25 == 0:
                cfg.progress(ex, "verify", done, len(syms))
    days = [(s, d) for s in syms for d in cfg.day_list if os.path.exists(cfg.path(ex, s, d + ".csv"))]
    rng = random.Random(14112026)
    spot = []
    for s, d in rng.sample(days, min(5, len(days))):
        exp = rest_day_lines(ex, s, d)
        with open(cfg.path(ex, s, d + ".csv"), newline="") as f:
            got = f.read().split("\n")[1:-1]
        diff = next((i for i, (a, b) in enumerate(zip(got, exp)) if a != b), None)
        spot.append(dict(symbol=s, day=d, rows_file=len(got), rows_rest=len(exp), identical=(got == exp), first_diff_row=diff))
        log(f"{ex} spot-check {s} {d}: identical={got == exp} file={len(got)} rest={len(exp)}")
    mism = sum(len(r["mismatches_1d_vs_1m"]) for r in out.values())
    ov = None if cfg.tag else cfg.jload(ex, "overlap.json")
    skipped = {b: [r["binance"] for r in ov["overlap"] if r["bybit"] == b][0] for b in ov["skipped"]} if ov else {}
    manifest = dict(
        exchange=ex, market="USDT-margined perpetuals" + (" (Binance USD-M; incl. TRADIFI_PERPETUAL)" if ex == "binance" else " (Bybit v5 linear, LinearPerpetual)"),
        source=("data.binance.vision monthly/daily zips (sha256 vs .CHECKSUM), REST for unpublished days; filter from REST 1d klines" if ex == "binance"
                else "api.bybit.com /v5/market/kline (1m, 1d), 1000 per request"),
        window=dict(start=day_str(cfg.start), full_days=cfg.days, last_full_day=day_str(cfg.end - DAY)), partial_day=part,
        filter=dict(rule=f"quote turnover of one full UTC day >= {cfg.thr} USDT on at least one day of the window (1d klines)", threshold=str(cfg.thr),
                    listed_usdt_perps_total=cfg.jload(ex, "symbols.json")["listed_usdt_perps_total"], candidates_with_window_data=len(flt),
                    qualifying=sum(f["qualifies"] for f in flt.values()), downloaded=len(syms)),
        totals=dict(rows=sum(r["rows"] for r in out.values()), symbol_days=len(days), gap_minutes=sum(r["gap_minutes"] for r in out.values()),
                    symbols_with_gaps=sum(1 for r in out.values() if r["gaps"]), symbol_days_not_1440=sum(len(r["rows_by_day_not_1440"]) for r in out.values()),
                    missing_expected_days=sum(len(r["missing_days"]) for r in out.values()), mismatches_1d_vs_1m=mism,
                    anomalies=sum(len(r["anomalies"]) for r in out.values())),
        spot_check=spot,
        skipped_as_on_binance=skipped,
        symbols={s: dict(meta[s], **flt[s], downloaded=(s in out), **out.get(s, {})) for s in sorted(flt)},
        written=iso(int(time.time() * 1000)))
    cfg.jsave(ex, cfg.name("manifest.json"), manifest)
    t = manifest["totals"]
    log(f"{ex} manifest: symbols {len(syms)}, rows {t['rows']}, gap minutes {t['gap_minutes']}, not-1440 days {t['symbol_days_not_1440']}, "
        f"1d-vs-1m mismatches {t['mismatches_1d_vs_1m']}, spot-check {sum(s['identical'] for s in spot)}/{len(spot)} identical")


# ---------------------------------------------------------------- funding

FUNDING_HEADER = {"binance": "funding_time,funding_rate,mark_price,rate_type", "bybit": "funding_time,funding_rate"}


def bn_funding_lines(sym, start, until):
    out, t = [], start
    while True:
        rows = BN.get(f"/fapi/v1/fundingRate?symbol={sym}&startTime={t}&endTime={until}&limit=1000", 1, spacing=1.25)
        out += [f"{r['fundingTime']},{r['fundingRate']},{r['markPrice']},{r.get('rateType', '')}" for r in rows]
        if len(rows) < 1000:
            return out
        t = rows[-1]["fundingTime"] + 1


def bb_funding_lines(sym, start, until):
    rows, end = {}, until
    while True:
        r = BB.get(f"/v5/market/funding/history?category=linear&symbol={sym}&startTime={start}&endTime={end}&limit=200")["list"]
        for x in r:
            rows[int(x["fundingRateTimestamp"])] = f"{x['fundingRateTimestamp']},{x['fundingRate']}"
        if len(r) < 200:
            return [rows[k] for k in sorted(rows)]
        end = min(int(x["fundingRateTimestamp"]) for x in r) - 1


def funding_step(cfg, ex):
    syms = selected(cfg, ex)
    start = day_ms(cfg.funding_from)
    fn = bn_funding_lines if ex == "binance" else bb_funding_lines
    todo = [s for s in syms if cfg.force or not os.path.exists(cfg.path(ex, s, "funding.csv"))]
    run = cfg.jload(ex, "funding_run.json")
    if run and len(todo) < len(syms):                  # resumed or already done: keep the recorded cut-off
        until = run["until_ms"]
    else:
        until = int(time.time() * 1000)
        cfg.jsave(ex, "funding_run.json", dict(from_ms=start, until_ms=until, until_utc=iso(until)))
    log(f"{ex} funding: {len(syms)} symbols, {len(todo)} to do, {cfg.funding_from} .. {iso(until)}")
    done = [len(syms) - len(todo)]

    def one(sym):
        lines = fn(sym, start, until)
        write_atomic(cfg.path(ex, sym, "funding.csv"), FUNDING_HEADER[ex] + "\n" + "".join(l + "\n" for l in lines))
        done[0] += 1
        if done[0] % 25 == 0 or done[0] == len(syms):
            log(f"{ex} funding {done[0]}/{len(syms)}")
            cfg.progress(ex, "funding", done[0], len(syms))

    with ThreadPoolExecutor(1 if ex == "binance" else 3) as pool:
        for f in [pool.submit(one, s) for s in todo]:
            f.result()
    stats, anomalies = {}, []
    for s in syms:
        with open(cfg.path(ex, s, "funding.csv"), newline="") as f:
            lines = f.read().split("\n")
        if lines[0] != FUNDING_HEADER[ex] or lines[-1] != "":
            anomalies.append(f"{s}: bad header/trailer")
        ts = [int(l.split(",", 1)[0]) for l in lines[1:-1]]
        if ts != sorted(set(ts)) or (ts and (ts[0] < start or ts[-1] > until)):
            anomalies.append(f"{s}: not strictly ascending or outside [from, until]")
        hrs = [round((b - a) / 3_600_000, 2) for a, b in zip(ts, ts[1:])]
        mode = max(set(hrs), key=hrs.count) if hrs else None
        stats[s] = dict(rows=len(ts), first=iso(ts[0]) if ts else None, last=iso(ts[-1]) if ts else None,
                        interval_hours_mode=mode, interval_hours_other={str(h): hrs.count(h) for h in sorted(set(hrs)) if h != mode},
                        hours_since_last=round((until - ts[-1]) / 3_600_000, 2) if ts else None)
    rng = random.Random(14112026)
    spot = []
    for s in rng.sample(syms, min(5, len(syms))):
        exp = fn(s, start, until)
        with open(cfg.path(ex, s, "funding.csv"), newline="") as f:
            got = f.read().split("\n")[1:-1]
        spot.append(dict(symbol=s, rows_file=len(got), rows_rest=len(exp), identical=(got == exp)))
        log(f"{ex} funding spot-check {s}: identical={got == exp} file={len(got)} rest={len(exp)}")
    manifest = dict(exchange=ex, source=("api: fapi.binance.com /fapi/v1/fundingRate, 1000 per request, paced 1.25 s (500/5min/IP shared limit)" if ex == "binance"
                                          else "api.bybit.com /v5/market/funding/history, 200 per request"),
                    header=FUNDING_HEADER[ex], from_utc=iso(start), until_utc=iso(until), symbols=stats, anomalies=anomalies,
                    totals=dict(symbols=len(syms), rows=sum(v["rows"] for v in stats.values()), symbols_without_rows=sum(1 for v in stats.values() if not v["rows"])),
                    spot_check=spot, written=iso(int(time.time() * 1000)))
    cfg.jsave(ex, "funding_manifest.json", manifest)
    t = manifest["totals"]
    log(f"{ex} funding manifest: symbols {t['symbols']}, rows {t['rows']}, no-row symbols {t['symbols_without_rows']}, anomalies {len(anomalies)}, "
        f"spot-check {sum(x['identical'] for x in spot)}/{len(spot)} identical")


def firstday_step(cfg, ex):
    """First UTC day with quote turnover >= threshold per window: August (filter.aug.json, daily --tag .aug), warmup (filter.warmup.json),
    main (filter.json); overall = the earliest."""
    main = cfg.jload(ex, "filter.json")
    wins = [cfg.jload(ex, f"filter.{t}.json", {}) for t in ("aug", "warmup")] + [main]
    lines = ["symbol,first_day_ge_aug_window,first_day_ge_warmup_window,first_day_ge_main_window,first_day_ge_overall"]
    for sym in selected(cfg, ex):
        d = [w.get(sym, {}).get("first_day_ge") or "" for w in wins]
        lines.append(f"{sym},{','.join(d)},{min((x for x in d if x), default='')}")
    write_atomic(cfg.path(ex, "first_qualifying_day.csv"), "\n".join(lines) + "\n")
    log(f"{ex} first_qualifying_day.csv: {len(lines) - 1} symbols")


# ---------------------------------------------------------------- main

def per_exchange(fn, cfg, exs):
    with ThreadPoolExecutor(len(exs)) as pool:
        for f in [pool.submit(fn, cfg, ex) for ex in exs]:
            f.result()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("step", choices=["symbols", "daily", "overlap", "fetch", "partial", "verify", "funding", "firstday", "prep", "download", "warmup", "all"])
    p.add_argument("--root", default="/data/klines")
    p.add_argument("--start", default="2026-09-15")
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--threshold", type=Decimal, default=Decimal(100_000_000))
    p.add_argument("--exchanges", default="binance,bybit")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--only", default="", help="comma list of symbols (tests)")
    p.add_argument("--force", action="store_true")
    p.add_argument("--tag", default="", help="suffix of the state files, e.g. .warmup (symbols still come from the main run)")
    p.add_argument("--funding-from", default="2026-09-01")
    a = p.parse_args()
    cfg = Cfg(a)
    exs = a.exchanges.split(",")
    sym_fn = {"binance": symbols_binance, "bybit": symbols_bybit}
    groups = {"prep": ["symbols", "daily", "overlap"], "download": ["fetch", "partial", "verify"],
              "warmup": ["daily", "fetch", "verify"], "all": ["symbols", "daily", "overlap", "fetch", "partial", "verify"]}
    if a.step == "warmup" and not a.tag:
        raise SystemExit("warmup needs --tag .warmup (and --start/--days of the warmup window)")
    steps = groups.get(a.step, [a.step])
    for step in steps:
        log(f"step {step} start")
        if step == "symbols":
            per_exchange(lambda c, ex: sym_fn[ex](c), cfg, exs)
        elif step == "daily":
            per_exchange(daily_step, cfg, exs)
        elif step == "overlap":
            overlap_step(cfg)
        elif step == "fetch":
            per_exchange(fetch_step, cfg, exs)
        elif step == "partial":
            per_exchange(partial_step, cfg, exs)
        elif step == "verify":
            per_exchange(verify_step, cfg, exs)
        elif step == "funding":
            per_exchange(funding_step, cfg, exs)
        elif step == "firstday":
            per_exchange(firstday_step, cfg, exs)
        log(f"step {step} done")


if __name__ == "__main__":
    sys.exit(main())
