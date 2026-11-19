#!/usr/bin/env python3
"""TK-581: текст `sar` (секции -u ALL, -q, -r, -B, -w, -d -p, -n DEV/EDEV/TCP/ETCP/SOCK/SOFT, -I SUM, -S) -> один CSV по 10-с слотам.
python3 sar_csv.py sar_full.txt out.csv"""
import sys, csv, re, collections
rows = collections.OrderedDict()
sec = None
def put(t, k, v):
    rows.setdefault(t, collections.OrderedDict())[k] = v
HDR = None
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    line = line.rstrip("\n")
    if line.startswith("#### sar "):
        sec = line[9:]
        HDR = None
        continue
    p = line.split()
    if not p or p[0] == "Linux" or p[0] == "Average:":
        continue
    if not re.match(r"\d\d:\d\d:\d\d", p[0]):
        continue
    t = p[0]
    if len(p) > 1 and not re.match(r"^[\d.\-]+$", p[1]) or (sec == "-u ALL" and p[1] in ("CPU",)):
        # заголовок: p[1] нечисловое
        if p[1] in ("all",) or p[1] in ("eth0", "lo", "sr0", "vda", "sum"):
            pass
        else:
            HDR = p[1:] if sec not in ("-n DEV", "-n EDEV") else p[2:]
            if sec in ("-u ALL",):
                HDR = p[2:]
            if sec == "-I SUM":
                HDR = None
            continue
    v = p[1:]
    if sec == "-u ALL":
        if v[0] != "all": continue
        for k, x in zip(HDR, v[1:]): put(t, "cpu_" + k.strip("%"), x)
    elif sec in ("-q", "-r", "-B", "-w", "-n TCP", "-n ETCP", "-n SOCK", "-S"):
        if HDR is None: continue
        pre = {"-q": "q_", "-r": "m_", "-B": "pg_", "-w": "w_", "-n TCP": "tcp_", "-n ETCP": "etcp_", "-n SOCK": "sock_", "-S": "swp_"}[sec]
        for k, x in zip(HDR, v): put(t, pre + k, x)
    elif sec == "-d -p":
        if v[-1] != "vda" or HDR is None: continue
        for k, x in zip(HDR, v[:-1]): put(t, "vda_" + k, x)
    elif sec in ("-n DEV", "-n EDEV"):
        if v[0] != "eth0" or HDR is None: continue
        for k, x in zip(HDR, v[1:]): put(t, "eth0_" + k, x)
    elif sec == "-n SOFT":
        pass
    elif sec == "-I SUM":
        if v[0] == "sum": put(t, "intr_sum", v[1])
cols = []
for r in rows.values():
    for k in r:
        if k not in cols: cols.append(k)
with open(sys.argv[2], "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["t"] + cols)
    for t, r in rows.items():
        w.writerow([t] + [r.get(c, "") for c in cols])
print(len(rows), "slots,", len(cols), "cols")
