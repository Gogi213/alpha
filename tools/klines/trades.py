#!/usr/bin/env python3
"""Raw trade files, stored exactly as published (no recompression), stdlib only.

  trades.py binance   data.binance.vision futures/um/daily/aggTrades zips of the Binance u12 universe (klines/binance/filter.u12.json),
                      each zip checked against its .CHECKSUM (sha256), kept next to it
                      -> <root>/binance/<SYMBOL>/<SYMBOL>-aggTrades-<YYYY-MM-DD>.zip (+ .CHECKSUM)
  trades.py bybit     public.bybit.com/trading/<SYMBOL>/<SYMBOL><day>.csv.gz of the Bybit symbols not on Binance
                      (klines/bybit/overlap.u12.json bybit_download); no checksums published: size == Content-Length and the whole gzip
                      stream (CRC) is read back while downloading
                      -> <root>/bybit/<SYMBOL>/<SYMBOL><YYYY-MM-DD>.csv.gz
Options: --plan-only (listing + sizes, prints the totals line and exits), --max-mbps (total download cap, default 320 Mbit/s).
Resumable: a file exists only after it was verified (tmp + rename). Downloads pause while the collectors' lag_in p99
(http://127.0.0.1:8088/api/v3, lag_in p99 or a bad flag) is above --lag-ms. Writes <root>/<exchange>/{plan.json,manifest.json}.
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
import sys
import threading
import time
import urllib.parse
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

DAY = 86_400_000
MIN = 60_000
S3 = "s3-ap-northeast-1.amazonaws.com"
_lock = threading.Lock()


def log(msg):
    with _lock:
        print(f"{time.strftime('%H:%M:%S', time.gmtime())} {msg}", flush=True)


def day_ms(s):
    return int(dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def day_str(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}.{threading.get_ident()}"
    with open(tmp, "wb" if isinstance(data, bytes) else "w") as f:
        f.write(data)
    os.replace(tmp, path)


class Throttle:
    """Total byte rate over all threads (leaky bucket) + pause while the collectors lag."""

    def __init__(self, bytes_per_s, lag_ms):
        self.rate = bytes_per_s
        self.lag_ms = lag_ms
        self.next = 0.0
        self.lock = threading.Lock()
        self.checked = 0.0
        self.paused = False
        self.pauses = 0

    def take(self, n):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next)
            self.next = t + n / self.rate
        if t > now:
            time.sleep(t - now)

    def lagging(self):
        """Worst collector lag_in p99 (ms) from the health page API; a collector flagged not ok / live_bad / late_bad counts as infinite lag."""
        try:
            c = http.client.HTTPConnection("127.0.0.1", 8088, timeout=5)
            c.request("GET", "/api/v3")
            rows = json.loads(c.getresponse().read())["rows"]
        except (OSError, ValueError, KeyError, http.client.HTTPException):
            return 0.0
        worst = 0.0
        for r in rows:
            if not r.get("ok", True) or r.get("live_bad") or r.get("late_bad"):
                return float("inf")
            worst = max(worst, r.get("detail", {}).get("lag_in", [0, 0])[1])
        return worst

    def gate(self):
        """Call before each file: wait while the collectors' lag p99 is above the limit."""
        while True:
            now = time.monotonic()
            with self.lock:
                due = now - self.checked > 5
                if due:
                    self.checked = now
            if due:
                w = self.lagging()
                self.paused = w > self.lag_ms
                if self.paused:
                    self.pauses += 1
                    log(f"collectors lag p99 {w:.0f} ms > {self.lag_ms}: download paused")
            if not self.paused:
                return
            time.sleep(15)
            self.checked = 0.0


class Http:
    """min_interval: global pacing of requests of this client; probe_path: a URL that must answer 200 - a 403 elsewhere while the probe
    also answers 403 means the host blocked us (public.bybit.com does after a burst): wait 2 min and retry instead of giving up."""

    def __init__(self, host, min_interval=0.0, probe_path=None):
        self.host = host
        self.local = threading.local()
        self.min_interval = min_interval
        self.probe_path = probe_path
        self.pace_lock = threading.Lock()
        self.pace_next = 0.0
        self.blocks = 0

    def _pace(self):
        if self.min_interval:
            with self.pace_lock:
                t = max(time.monotonic(), self.pace_next)
                self.pace_next = t + self.min_interval
            time.sleep(max(0.0, t - time.monotonic()))

    def _blocked(self):
        c = http.client.HTTPSConnection(self.host, timeout=30)
        try:
            c.request("GET", self.probe_path, headers={"User-Agent": "klines-tools/1.0"})
            r = c.getresponse()
            r.read()
            return r.status == 403
        except (OSError, http.client.HTTPException):
            return False
        finally:
            c.close()

    def _conn(self):
        c = getattr(self.local, "c", None)
        if c is None:
            c = self.local.c = http.client.HTTPSConnection(self.host, timeout=60)
        return c

    def request(self, method, path, sink=None, throttle=None):
        """-> (status, headers, body bytes); with sink(chunk) the body is streamed to it instead (body = b'')."""
        path = urllib.parse.quote(path, safe="/?&=%:.-_~")
        last = None
        attempt = 0
        while attempt < 6:
            attempt += 1
            try:
                self._pace()
                c = self._conn()
                c.request(method, path, headers={"User-Agent": "klines-tools/1.0"})
                r = c.getresponse()
                h = {k.lower(): v for k, v in r.getheaders()}
                if r.status == 403 and self.probe_path and self.blocks < 10 and self._blocked():
                    r.read()
                    self.blocks += 1
                    attempt -= 1
                    log(f"{self.host} answers 403 to the probe too: blocked, waiting 120 s (block {self.blocks})")
                    time.sleep(120)
                    continue
                if sink is None or r.status != 200:
                    return r.status, h, r.read()
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        return r.status, h, b""
                    if throttle:
                        throttle.take(len(chunk))
                    sink(chunk)
            except (OSError, http.client.HTTPException, zlib.error) as e:
                last = e
                self.local.c = None
                if sink is not None:
                    raise StreamRetry(str(e)) from e
                time.sleep(min(2 ** attempt, 30))
        raise RuntimeError(f"{method} {self.host}{path}: {last}")


class StreamRetry(Exception):
    pass


def s3_zip_sizes(h3, sym, start_day, end_ms):
    """{day: size} and set of days having a .CHECKSUM of futures/um/daily/aggTrades/<sym> inside [start, end)."""
    pre = f"data/futures/um/daily/aggTrades/{sym}/{sym}-aggTrades-"
    marker = f"{pre}{day_str(day_ms(start_day) - DAY)}.zip.CHECKSUM"
    sizes, sums, token = {}, set(), marker
    while True:
        st, _, body = h3.request("GET", f"/data.binance.vision?prefix={urllib.parse.quote(pre)}&marker={urllib.parse.quote(token)}&max-keys=1000")
        if st != 200:
            raise RuntimeError(f"s3 listing {sym}: {st}")
        x = body.decode()
        for key, size in re.findall(r"<Contents><Key>([^<]+)</Key>.*?<Size>(\d+)</Size>", x):
            m = re.search(r"-aggTrades-(\d{4}-\d\d-\d\d)\.zip(\.CHECKSUM)?$", key)
            if m and start_day <= m.group(1) < day_str(end_ms):
                (sums.add(m.group(1)) if m.group(2) else sizes.__setitem__(m.group(1), int(size)))
        if "<IsTruncated>true" in x:
            token = re.findall(r"<Key>([^<]+)</Key>", x)[-1]
        else:
            return sizes, sums


def plan_binance(a):
    klines = a.klines
    flt = json.load(open(f"{klines}/binance/filter.u12.json"))
    daily = json.load(open(f"{klines}/binance/daily.u12.json"))
    syms = sorted(s for s, f in flt.items() if f["qualifies"])
    if a.only:
        syms = [s for s in syms if s in a.only]
    end = day_ms(a.start) + a.days * DAY
    h3 = Http(S3)
    plan = {}

    def one(sym):
        sizes, sums = s3_zip_sizes(h3, sym, a.start, end)
        active = {d for d, r in daily.get(sym, {}).items() if r[8] != "0" and a.start <= d < day_str(end)}
        return sym, dict(days=dict(sorted(sizes.items())), bytes=sum(sizes.values()), no_checksum_file=sorted(set(sizes) - sums),
                         missing_days=sorted(active - set(sizes)), listed_without_trades=sorted(set(sizes) - active))

    with ThreadPoolExecutor(8) as pool:
        for sym, p in pool.map(one, syms):
            plan[sym] = p
    return plan


def plan_bybit(a):
    ov = json.load(open(f"{a.klines}/bybit/overlap.u12.json"))
    daily = json.load(open(f"{a.klines}/bybit/daily.u12.json"))
    syms = sorted(ov["bybit_download"])
    if a.only:
        syms = [s for s in syms if s in a.only]
    end = day_ms(a.start) + a.days * DAY
    h = Http("public.bybit.com", min_interval=0.2, probe_path="/trading/")      # 5 requests/s in total

    def one(sym):
        st, _, body = h.request("GET", f"/trading/{sym}/")
        if st in (403, 404):                      # no such directory on public.bybit.com: nothing published for this symbol
            return sym, dict(days={}, bytes=0, missing_days=sorted(d for d, r in daily.get(sym, {}).items() if Decimal(r[6]) > 0 and a.start <= d < day_str(end)),
                             listed_without_trades=[], listing_status=st)
        if st != 200:
            raise RuntimeError(f"bybit listing {sym}: {st}")
        days = sorted(d for d in re.findall(rf'href="{re.escape(sym)}(\d{{4}}-\d\d-\d\d)\.csv\.gz"', body.decode()) if a.start <= d < day_str(end))
        sizes, head_fail = {}, {}
        for d in days:
            st, hd, _ = h.request("HEAD", f"/trading/{sym}/{sym}{d}.csv.gz")
            if st == 200:
                sizes[d] = int(hd["content-length"])
            else:
                head_fail[d] = st                 # listed but not served (e.g. 403): reported, not downloaded
        active = {d for d, r in daily.get(sym, {}).items() if Decimal(r[6]) > 0 and a.start <= d < day_str(end)}
        return sym, dict(days=sizes, bytes=sum(sizes.values()), missing_days=sorted(active - set(sizes)), listed_without_trades=sorted(set(sizes) - active),
                         listed_but_not_served=head_fail)

    with ThreadPoolExecutor(3) as pool:
        return dict(pool.map(one, syms))


def get_binance_file(h, th, root, sym, day, size):
    name = f"{sym}-aggTrades-{day}.zip"
    dest = os.path.join(root, "binance", sym, name)
    if os.path.exists(dest) and os.path.getsize(dest) == size and os.path.exists(dest + ".CHECKSUM"):
        return "have", size
    path = f"/data/futures/um/daily/aggTrades/{sym}/{name}"
    for attempt in range(3):
        th.gate()
        st, _, cs = h.request("GET", path + ".CHECKSUM")
        if st != 200:
            raise RuntimeError(f"{name}.CHECKSUM: {st}")
        want = cs.decode().split()[0]
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = f"{dest}.tmp{threading.get_ident()}"
        sha, n = hashlib.sha256(), 0
        with open(tmp, "wb") as f:
            def sink(chunk):
                f.write(chunk)
                sha.update(chunk)
            try:
                st, _, _ = h.request("GET", path, sink=sink, throttle=th)
            except StreamRetry:
                st = 0
        n = os.path.getsize(tmp)
        if st == 200 and sha.hexdigest() == want and n == size:
            os.replace(tmp, dest)
            write_atomic(dest + ".CHECKSUM", cs)
            return "ok", n
        os.remove(tmp)
        log(f"{name}: attempt {attempt + 1} failed (status {st}, {n} of {size} bytes, sha {'ok' if sha.hexdigest() == want else 'bad'})")
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{name}: failed after 3 attempts")


def get_bybit_file(h, th, root, sym, day, size):
    name = f"{sym}{day}.csv.gz"
    dest = os.path.join(root, "bybit", sym, name)
    if os.path.exists(dest) and os.path.getsize(dest) == size:
        return "have", size, None, None
    path = f"/trading/{sym}/{name}"
    for attempt in range(3):
        th.gate()
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = f"{dest}.tmp{threading.get_ident()}"
        sha, z, rows = hashlib.sha256(), zlib.decompressobj(31), [0]
        with open(tmp, "wb") as f:
            def sink(chunk):
                f.write(chunk)
                sha.update(chunk)
                rows[0] += z.decompress(chunk).count(b"\n")      # raises on a corrupt stream; CRC/length checked at the end of the member
            try:
                st, _, _ = h.request("GET", path, sink=sink, throttle=th)
            except StreamRetry:
                st = 0
        n = os.path.getsize(tmp)
        if st == 200 and z.eof and not z.unused_data and n == size:
            os.replace(tmp, dest)
            return "ok", n, sha.hexdigest(), rows[0] - 1
        os.remove(tmp)
        log(f"{name}: attempt {attempt + 1} failed (status {st}, {n} of {size} bytes, gzip complete {z.eof})")
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{name}: failed after 3 attempts")


def download(a, ex, plan):
    th = Throttle(a.max_mbps * 1e6 / 8, a.lag_ms)
    h = Http("data.binance.vision") if ex == "binance" else Http("public.bybit.com", min_interval=0.25, probe_path="/trading/")
    tasks = [(s, d, sz) for s, p in plan.items() for d, sz in p["days"].items()]
    total = sum(t[2] for t in tasks)
    log(f"{ex} download: {len(tasks)} files, {total / 1e9:.1f} GB, cap {a.max_mbps} Mbit/s, {a.workers} workers")
    done, got, t0 = [0, 0], {}, time.time()

    def one(t):
        s, d, sz = t
        r = (get_binance_file if ex == "binance" else get_bybit_file)(h, th, a.root, s, d, sz)
        with _lock:
            done[0] += 1
            done[1] += sz
            if done[0] % 500 == 0:
                el = time.time() - t0
                print(f"{time.strftime('%H:%M:%S', time.gmtime())} {ex} {done[0]}/{len(tasks)} files, {done[1] / 1e9:.1f}/{total / 1e9:.1f} GB, {done[1] / el / 1e6:.1f} MB/s avg", flush=True)
                write_atomic(os.path.join(a.root, "progress.json"), json.dumps({ex: dict(files=done[0], of=len(tasks), gb=round(done[1] / 1e9, 2), of_gb=round(total / 1e9, 2),
                                                                                    pct=round(100 * done[1] / max(total, 1), 1))}))
        return t, r

    with ThreadPoolExecutor(a.workers) as pool:
        for t, r in pool.map(one, tasks):
            got[(t[0], t[1])] = r
    return got, th, time.time() - t0


def verify_binance(a, plan, rng):
    """Spot checks: zip CRC of 10 random files; 5 random symbols on the last window day: first 100 rows against REST /fapi/v1/aggTrades
    (REST serves only the last 2 days); 3 random symbol-days (files <= 60 MB): quantity sums against the 1m kline volume on disk and the id sequence (no holes)."""
    files = [(s, d) for s, p in plan.items() for d in p["days"]]
    crc = []
    for s, d in rng.sample(files, min(10, len(files))):
        z = zipfile.ZipFile(os.path.join(a.root, "binance", s, f"{s}-aggTrades-{d}.zip"))
        crc.append(dict(symbol=s, day=d, testzip=z.testzip()))
    rest = []
    fapi = Http("fapi.binance.com")
    last_day = day_str(day_ms(a.start) + (a.days - 1) * DAY)       # REST aggTrades only serves the most recent 2 days (-4166)
    recent = [(s, d) for s, d in files if d == last_day]
    for s, d in rng.sample(recent, min(5, len(recent))):
        z = zipfile.ZipFile(os.path.join(a.root, "binance", s, f"{s}-aggTrades-{d}.zip"))
        with z.open(z.namelist()[0]) as f:
            rows = []
            for raw in io.TextIOWrapper(f):
                if raw[0].isdigit():
                    rows.append(raw.rstrip("\r\n"))
                if len(rows) == 100:
                    break
        first = int(rows[0].split(",")[0])
        time.sleep(1.5)
        st, _, body = fapi.request("GET", f"/fapi/v1/aggTrades?symbol={s}&fromId={first}&limit=100")
        def norm(c):          # REST prints "40", the file "40.0": compare price/quantity as numbers, the rest as text
            return [c[0], Decimal(c[1]), Decimal(c[2]), c[3], c[4], c[5], c[6]]
        exp = [[str(r["a"]), Decimal(r["p"]), Decimal(r["q"]), str(r["f"]), str(r["l"]), str(r["T"]), str(r["m"]).lower()] for r in json.loads(body)] if st == 200 else None
        rest.append(dict(symbol=s, day=d, rows=len(rows), identical=(exp == [norm(r.split(",")) for r in rows]), rest_status=st))
    vs_klines = []
    small = [(s, d) for s, d in files if plan[s]["days"][d] <= 60_000_000 and os.path.exists(f"{a.klines}/binance/{s}/{d}.csv")]
    for s, d in rng.sample(small, min(3, len(small))):
        per, id_breaks, trade_gaps, prev_id, prev_last, rows_n = {}, 0, 0, None, None, 0
        with zipfile.ZipFile(os.path.join(a.root, "binance", s, f"{s}-aggTrades-{d}.zip")) as z, z.open(z.namelist()[0]) as f:
            for raw in io.TextIOWrapper(f):
                if raw[0].isdigit():
                    c = raw.split(",")
                    m = int(c[5]) // MIN * MIN
                    per[m] = per.get(m, Decimal(0)) + Decimal(c[2])
                    rows_n += 1
                    if prev_id is not None:
                        id_breaks += int(c[0]) != prev_id + 1          # agg_trade_id must run without holes
                        trade_gaps += int(c[3]) != prev_last + 1       # trade ids not covered by any aggregate (informational)
                    prev_id, prev_last = int(c[0]), int(c[4])
        kl = {}
        for line in open(f"{a.klines}/binance/{s}/{d}.csv").read().split("\n")[1:]:
            if line:
                c = line.split(",")
                kl[int(c[0])] = Decimal(c[5])
        bad = [m for m in kl if per.get(m, Decimal(0)) != kl[m]] + [m for m in per if m not in kl]
        # an aggregate trade is stamped with its first trade's time and can straddle a minute or day boundary, so quantities move to the
        # neighbouring minute/day: the totals agree to a relative difference of ~1e-6, the agg_trade_id sequence is the exact completeness check
        tk, tp = sum(kl.values()), sum(per.values())
        vs_klines.append(dict(symbol=s, day=d, rows=rows_n, agg_id_breaks=id_breaks, trade_id_gaps=trade_gaps, minutes=len(kl), minutes_differing=len(bad),
                              day_total_rel_diff=float((tp - tk) / tk) if tk else None))
    return dict(zip_crc=crc, rest_first_100_rows=rest, quantity_per_minute_vs_1m_volume=vs_klines)


def verify_bybit(a, plan, rng):
    """Spot checks: 3 random symbol-days (files <= 60 MB): sum of size per minute against the 1m kline volume on disk."""
    small = [(s, d) for s, p in plan.items() for d, sz in p["days"].items() if sz <= 60_000_000 and os.path.exists(f"{a.klines}/bybit/{s}/{d}.csv")]
    out = []
    for s, d in rng.sample(small, min(3, len(small))):
        import gzip
        per, hdr = {}, None
        with gzip.open(os.path.join(a.root, "bybit", s, f"{s}{d}.csv.gz"), "rt") as f:
            hdr = f.readline().rstrip("\n").split(",")
            ti, si = hdr.index("timestamp"), hdr.index("size")
            for raw in f:
                c = raw.split(",")
                m = int(Decimal(c[ti]) * 1000) // MIN * MIN
                per[m] = per.get(m, Decimal(0)) + Decimal(c[si])
        kl = {}
        for line in open(f"{a.klines}/bybit/{s}/{d}.csv").read().split("\n")[1:]:
            if line:
                c = line.split(",")
                kl[int(c[0])] = Decimal(c[5])
        bad = [m for m in kl if per.get(m, Decimal(0)) != kl[m]] + [m for m in per if m not in kl]
        out.append(dict(symbol=s, day=d, header=hdr, minutes=len(kl), day_total_equal=(sum(per.values()) == sum(kl.values())), minutes_differing=len(bad)))
    return dict(size_per_minute_vs_1m_volume=out)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("exchange", choices=["binance", "bybit"])
    p.add_argument("--root", default="/data/aggtrades")
    p.add_argument("--klines", default="/data/klines")
    p.add_argument("--start", default="2026-09-01")
    p.add_argument("--days", type=int, default=74)
    p.add_argument("--max-mbps", type=float, default=320.0)
    p.add_argument("--lag-ms", type=float, default=2500.0)
    p.add_argument("--workers", type=int, default=8, help="parallel files (Bybit: keep <= 6)")
    p.add_argument("--only", default="")
    p.add_argument("--plan-only", action="store_true")
    a = p.parse_args()
    a.only = set(filter(None, a.only.split(",")))
    ex = a.exchange
    t0 = int(time.time() * 1000)
    plan = (plan_binance if ex == "binance" else plan_bybit)(a)
    files = sum(len(v["days"]) for v in plan.values())
    total = sum(v["bytes"] for v in plan.values())
    write_atomic(os.path.join(a.root, ex, "plan.json"), json.dumps(dict(window_start=a.start, days=a.days, symbols=plan, planned=iso(t0)), indent=1, sort_keys=True) + "\n")
    log(f"{ex} plan: symbols {len(plan)}, files {files}, {total / 1e9:.1f} GB, symbols with missing active days {sum(1 for v in plan.values() if v['missing_days'])}")
    if a.plan_only:
        return
    got, th, secs = download(a, ex, plan)
    rng = random.Random(14112026)
    ver = (verify_binance if ex == "binance" else verify_bybit)(a, plan, rng)
    shas = {f"{s}/{d}": r[2] for (s, d), r in got.items() if len(r) > 2 and r[2]}
    on_disk = sum(1 for s, v in plan.items() for d in v["days"] if os.path.exists(os.path.join(a.root, ex, s, (f"{s}-aggTrades-{d}.zip" if ex == "binance" else f"{s}{d}.csv.gz"))))
    manifest = dict(exchange=ex, source=("https://data.binance.vision/data/futures/um/daily/aggTrades/<SYMBOL>/ (zip as published, sha256 vs .CHECKSUM)" if ex == "binance"
                                          else "https://public.bybit.com/trading/<SYMBOL>/ (csv.gz as published; size == Content-Length, gzip CRC checked while downloading)"),
                    window=dict(start=a.start, days=a.days), started=iso(t0), finished=iso(int(time.time() * 1000)), seconds=round(secs),
                    totals=dict(symbols=len(plan), files_planned=files, files_on_disk=on_disk, bytes_planned=total, newly_downloaded=sum(1 for r in got.values() if r[0] == "ok"),
                                already_there=sum(1 for r in got.values() if r[0] == "have"), pauses_for_collector_lag=th.pauses,
                                symbols_with_missing_active_days=sum(1 for v in plan.values() if v["missing_days"])),
                    max_mbps=a.max_mbps, verification=ver, symbols={s: {k: v for k, v in pv.items() if k != "days"} | dict(days=len(pv["days"])) for s, pv in plan.items()})
    if ex == "bybit":
        manifest["sha256_gz"] = shas
    write_atomic(os.path.join(a.root, ex, "manifest.json"), json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    t = manifest["totals"]
    log(f"{ex} done: {t['files_on_disk']}/{t['files_planned']} files, {t['bytes_planned'] / 1e9:.1f} GB, {secs:.0f} s, missing-active-day symbols {t['symbols_with_missing_active_days']}")


if __name__ == "__main__":
    sys.exit(main())
