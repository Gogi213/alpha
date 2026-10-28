#!/usr/bin/env python3
"""TK-428: quanto_multiplier фьючерсов Gate, которых нет в gate-list (делистнуты до августа): GET /futures/usdt/contracts/<имя> (отдаёт и in_delisting).
Использование: tk428-gate-quanto.py <gate-missing.tsv> <выход-список.tsv> <выход-ответы.tsv>; список «рынок<TAB>инструмент<TAB>quanto» (спот — 1)."""
import json
import sys
import time
import os
import urllib.error
import urllib.parse
import urllib.request

miss = [l.rstrip("\n").split("\t") for l in open(sys.argv[1], encoding="utf-8")]
fut = sorted({i for k, m, i, b in miss if k == "fut"})
spot = sorted({i for k, m, i, b in miss if k == "spot"})
done = {l.split("	")[0] for l in open(sys.argv[3], encoding="utf-8")} if os.path.exists(sys.argv[3]) else set()  # дописывает, готовое пропускает
lst = open(sys.argv[2], "a", encoding="utf-8", newline="\n", buffering=1)
ans = open(sys.argv[3], "a", encoding="utf-8", newline="\n", buffering=1)
for c in fut:
    if c in done:
        continue
    try:
        j = json.loads(urllib.request.urlopen("https://api.gateio.ws/api/v4/futures/usdt/contracts/" + urllib.parse.quote(c), timeout=30).read())
        q = j.get("quanto_multiplier", "")
        ans.write("%s\t200\t%s\tin_delisting=%s\tcreate_time=%s\n" % (c, q, j.get("in_delisting"), j.get("create_time")))
        if q:
            lst.write("fut\t%s\t%s\n" % (c, q))
    except urllib.error.HTTPError as e:
        ans.write("%s\t%d\t\t%s\n" % (c, e.code, e.read()[:120].decode("utf-8", "replace").replace("\n", " ")))
    time.sleep(0.15)
for c in spot:
    lst.write("spot\t%s\t1\n" % c)
