#!/usr/bin/env python3
"""TK-428: полный список инструментов Gate с архивом сделок за месяц — S3-листинг бакета download.gatedata.org (prefix=<рынок>/<ГГГГММ>/).
Использование: tk428-gate-index.py <выход.tsv> [месяцы через запятую]; строки «рынок<TAB>месяц<TAB>инструмент<TAB>байт»."""
import re
import sys
import urllib.parse
import urllib.request

BASE = "https://gateio-public-data.s3.amazonaws.com/"  # download.gatedata.org (CDN) параметры листинга игнорирует
months = (sys.argv[2] if len(sys.argv) > 2 else "202601,202602,202603,202604,202605,202606,202607").split(",")
out = open(sys.argv[1], "w", encoding="utf-8", newline="\n")
for mk, name in (("futures_usdt/trades", "fut"), ("spot/deals", "spot")):
    for m in months:
        prefix = "%s/%s/" % (mk, m)
        marker, n, pages = "", 0, 0
        while True:
            q = {"prefix": prefix, "max-keys": "1000"}
            if marker:
                q["marker"] = marker
            x = urllib.request.urlopen(BASE + "?" + urllib.parse.urlencode(q), timeout=60).read().decode()
            pages += 1
            for key, size in re.findall(r"<Key>([^<]+)</Key>.*?<Size>(\d+)</Size>", x):
                inst = key[len(prefix):].rsplit("-" + m, 1)[0]
                out.write("%s\t%s\t%s\t%s\n" % (name, m, inst, size))
                n += 1
                marker = key
            if "<IsTruncated>true</IsTruncated>" not in x:
                break
        print(name, m, n, "страниц", pages, flush=True)
out.close()
