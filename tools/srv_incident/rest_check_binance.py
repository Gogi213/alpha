#!/usr/bin/env python3
"""TK-581: независимая сверка сделок Binance с REST биржи (`/fapi/v1/aggTrades`, не дозапрос коллектора) по горстке символов за окно по времени БИРЖИ.
Наша сторона: интервалы номеров сделок f..l по символу = записи ws шардов сделок (приём до конца окна скана) ∪ строки `rt` дозапроса (`backfill-binance-*.tsv.zst`).
REST: все агрегаты символа с T в [с, по]; агрегат «не покрыт», если его [f, l] не лежит целиком в наших интервалах.
Нагрузка на боевой IP: ≤ 1 запрос/с, символов мало (вес aggTrades 20, лимит 2400/мин). Только чтение.
python3 rest_check_binance.py <HH:MM:SS с> <HH:MM:SS по> <до скана HH:MM:SS> <день> <символ,символ,...>"""
import sys, glob, re, subprocess, json, time, urllib.request, datetime as dt

ROOT = "/data/collect/binance/data"
a, b, scan_to, day, syms = sys.argv[1:6]
SYMS = set(syms.split(","))
d0 = int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())
sec = lambda s: d0 + int(s[:2]) * 3600 + int(s[3:5]) * 60 + int(s[6:])
w0, w1, scan_end = sec(a), sec(b), sec(scan_to)


def merge(iv):
    iv.sort()
    out = []
    for lo, hi in iv:
        if out and lo <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return out


ours = {s: [] for s in SYMS}
ws_only = {s: [] for s in SYMS}
# ws: файлы шардов сделок — запуск с наибольшим номером <= начала окна
best = {}
for p in glob.glob(f"{ROOT}/{day}/trade*-*.tsv.zst"):
    m = re.match(r"trade(\d+)-(\d+)\.tsv\.zst$", p.split("/")[-1])
    if m and int(m.group(2)) <= w0 and (m.group(1) not in best or int(m.group(2)) > best[m.group(1)][0]):
        best[m.group(1)] = (int(m.group(2)), p)
SYMS_B = {x.encode() for x in SYMS}
nws = 0
for sh, (run, p) in sorted(best.items()):
    proc = subprocess.Popen(["zstd", "-dcq", p], stdout=subprocess.PIPE, bufsize=1 << 22)
    t = 0
    prev = {}
    for line in proc.stdout:
        f = line.split(b"\t", 10)
        try:
            t += int(f[1])
        except (ValueError, IndexError):
            continue
        if t >= scan_end * 1_000_000_000:
            break
        if f[0] != b"t" or len(f) < 10 or f[4] not in SYMS_B:
            continue
        sym = f[4]
        try:
            fc = int(f[9]); lf = int(f[8])
        except ValueError:
            continue
        fa = fc if sym not in prev else prev[sym] + 1 + fc
        la = fa + lf
        prev[sym] = la
        s = sym.decode()
        if s in SYMS:
            ours[s].append([fa, la])
            ws_only[s].append([fa, la])
            nws += 1
    proc.kill(); proc.wait()
nrt = 0
for p in glob.glob(f"{ROOT}/{day}/backfill-*.tsv.zst"):
    out = subprocess.run(["zstd", "-dcq", p], capture_output=True).stdout.decode("utf-8", "replace")
    for ln in out.split("\n"):
        q = ln.split("\t")
        if q[0] == "rt" and len(q) >= 9 and q[2] in SYMS:
            try:
                ours[q[2]].append([int(q[7]), int(q[8])]); nrt += 1
            except ValueError:
                pass
print(f"ws записей {nws}, rt строк {nrt}", flush=True)


def covered(iv, lo, hi):
    return any(x <= lo and hi <= y for x, y in iv)


tot = {"rest": 0, "missing_ws_only": 0, "missing": 0, "missing_ids": 0}
for s in sorted(SYMS):
    iv = merge(ours[s])
    iv_ws = merge(ws_only[s])
    rest = []
    start = w0 * 1000
    from_id = None
    for _ in range(40):
        url = f"https://fapi.binance.com/fapi/v1/aggTrades?symbol={s}&limit=1000" + (f"&fromId={from_id}" if from_id else f"&startTime={start}&endTime={w1 * 1000 + 999}")
        time.sleep(1.0)
        try:
            data = json.load(urllib.request.urlopen(url, timeout=20))
        except Exception as e:
            print(s, "REST ошибка", e); break
        if not data:
            break
        stop = False
        for x in data:
            if x["T"] > w1 * 1000 + 999:
                stop = True; break
            rest.append((x["a"], x["f"], x["l"], x["T"]))
        if stop or len(data) < 1000:
            break
        from_id = data[-1]["a"] + 1
    miss_ws = [r for r in rest if not covered(iv_ws, r[1], r[2])]
    miss = [r for r in rest if not covered(iv, r[1], r[2])]
    mi = sum(r[2] - r[1] + 1 for r in miss)
    tot["rest"] += len(rest); tot["missing_ws_only"] += len(miss_ws); tot["missing"] += len(miss); tot["missing_ids"] += mi
    print(f"{s}: REST агрегатов {len(rest)}, нет в ws {len(miss_ws)}, не покрыто ws ∪ rt {len(miss)} (номеров сделок {mi})" + (f"; первые: {[(r[0], r[1], r[2], dt.datetime.fromtimestamp(r[3]/1000, dt.timezone.utc).strftime('%H:%M:%S')) for r in miss[:4]]}" if miss else ""), flush=True)
print("ИТОГО", tot)
