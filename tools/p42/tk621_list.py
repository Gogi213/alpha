#!/usr/bin/env python3
"""TK-621: contract list "as it was" — all Binance USDT-M perpetuals with aggTrades on any day D0..D1.

Source: data.binance.vision S3 listing (daily aggTrades; keeps delisted contracts) + fapi exchangeInfo
(contractType, underlyingType, onboardDate, status — only for contracts still listed).
Run on srv: python3 tk621_list.py <out_dir> [D0 D1]   (default 2026-06-17 .. 2026-11-13)
Writes <out_dir>/contracts.json, <out_dir>/files.tsv (kind, symbol, day, size, etag).
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict

S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
NS = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}


def s3_list(prefix, delimiter=None, start_after=None):
    token = None
    while True:
        q = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if start_after:
            q["start-after"] = start_after
        if delimiter:
            q["delimiter"] = delimiter
        if token:
            q["continuation-token"] = token
        for attempt in range(8):
            try:
                with urllib.request.urlopen(f"{S3}?{urllib.parse.urlencode(q)}", timeout=60) as r:
                    root = ET.fromstring(r.read())
                break
            except OSError:
                if attempt == 7:
                    raise
                time.sleep(2 * (attempt + 1))
        for cp in root.findall("s:CommonPrefixes/s:Prefix", NS):
            yield ("dir", cp.text, 0, "")
        for c in root.findall("s:Contents", NS):
            yield ("file", c.find("s:Key", NS).text, int(c.find("s:Size", NS).text), c.find("s:ETag", NS).text.strip('"'))
        if root.find("s:IsTruncated", NS).text == "true":
            token = root.find("s:NextContinuationToken", NS).text
        else:
            break


def main():
    out = sys.argv[1]
    d0 = sys.argv[2] if len(sys.argv) > 2 else "2026-06-17"
    d1 = sys.argv[3] if len(sys.argv) > 3 else "2026-11-13"
    base = "data/futures/um/daily/"
    syms = sorted(
        p.split("/")[-2]
        for k, p, _, _ in s3_list(base + "aggTrades/", "/")
        if k == "dir"
    )
    # perpetual USDT: <BASE>USDT, no "_YYMMDD" delivery suffix
    syms = [s for s in syms if s.endswith("USDT") and "_" not in s]
    info = json.load(urllib.request.urlopen("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=60))
    ex = {s["symbol"]: s for s in info["symbols"]}
    rows, contracts = [], {}
    day_re = re.compile(r"-(\d{4}-\d{2}-\d{2})\.zip$")

    def one(s):
        files = {"aggTrades": {}, "klines": {}}
        for kind, pre, nm in (("aggTrades", f"{base}aggTrades/{s}/", f"{s}-aggTrades-"), ("klines", f"{base}klines/{s}/1m/", f"{s}-1m-")):
            for k, key, size, etag in s3_list(pre, start_after=f"{pre}{nm}{d0}"):
                m = day_re.search(key)
                if k == "file" and m and key.endswith(".zip") and d0 <= m.group(1) <= d1:
                    files[kind][m.group(1)] = (size, etag)
        return s, files

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(one, syms))
    for i, (s, files) in enumerate(results):
        if not files["aggTrades"]:
            continue
        for kind in files:
            for day, (size, etag) in sorted(files[kind].items()):
                rows.append((kind, s, day, size, etag))
        days = sorted(files["aggTrades"])
        e = ex.get(s, {})
        contracts[s] = {
            "first_day": days[0], "last_day": days[-1], "n_agg_days": len(days), "n_kl_days": len(files["klines"]),
            "agg_bytes": sum(v[0] for v in files["aggTrades"].values()),
            "kl_bytes": sum(v[0] for v in files["klines"].values()),
            "in_exchange_info": s in ex, "status": e.get("status"), "contract_type": e.get("contractType"),
            "underlying_type": e.get("underlyingType"), "underlying_sub_type": e.get("underlyingSubType"),
            "onboard_ms": e.get("onboardDate"), "delivery_ms": e.get("deliveryDate"),
        }
    json.dump({"window": [d0, d1], "n_s3_symbols": len(syms), "contracts": contracts,
               "exchange_info_symbols": len(ex)}, open(f"{out}/contracts.json", "w"), indent=1, sort_keys=True)
    with open(f"{out}/files.tsv", "w") as f:
        for r in rows:
            f.write("\t".join(map(str, r)) + "\n")
    print("contracts", len(contracts), "files", len(rows))


main()
