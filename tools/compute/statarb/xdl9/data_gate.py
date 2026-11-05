#!/usr/bin/env python3
"""TK-493 F1, ворота данных G1 / G1b(в): манифест полноты свечей и фандинга, скачанных скриптами `tools/xdl/` (формат — xdl_common).

Три подкоманды, только стандартная библиотека (numpy не нужен; на Steam Deck его нет):

  scan   — на машине, где лежат данные: по каждой бирже построить манифест (символ, класс, число свечей, первая/последняя минута,
           дыры, v=0, плохие бары, дубли/порядок, фандинг), суточное покрытие и минутный ряд (число символов в минуту + ряд BTC).
  merge  — склеить части одной биржи, посчитанные на разных машинах (Bitget докачивали две машины: scan --tag A / --tag B).
  report — на ПК: свести части всех бирж в manifest.csv, exchange_summary.csv, class_summary.csv, daily_matrix.csv,
           btc_daily_turnover.csv, outages.csv (скрининг выпадений: ни одной свечи ни у одного символа, число символов в минуту
           < 50 % медианы на 5+ минут, минуты без BTC, дни, когда оборот BTC биржи < 50 % от сверстников — пороги только для обзора, не критерии).

Примеры:
  nice -n 19 python3 data_gate.py scan --root ~/xdl/out --out ~/xdl/gate --exchanges binance,aster,kraken,lighter --procs 4
  nice -n 19 python3 data_gate.py scan --root /opt/xdl/out --out /opt/xdl/gate --exchanges bybit,okx,bingx,hyperliquid
  python3 data_gate.py merge --out DIR --exchange bitget --parts A,B
  python3 data_gate.py report --in DIR --out DIR

Окно по умолчанию — [2026-10-01 00:00, 2026-11-05 00:00) UTC, 35 суток = 50 400 минут (как у закачки). У Hyperliquid основная сетка —
15 минут (`candles15/`, 3 360 баров); его 1-минутный каталог `candles/` (окно неполное) сканируется отдельно, файлы с суффиксом `__candles`.

Правила проверок (строго, без допусков):
  bad_ohlc   — цена <= 0 или не число, либо high < max(open, close), либо low > min(open, close);
  дубль      — то же время подряд (в файле строки по возрастанию); порядок — время меньше предыдущего; вне сетки — время не кратно шагу;
  дыра       — пропущенные шаги между первой и последней свечой файла (хвост/голова окна считаются отдельно: missing_head/missing_tail);
  v=0        — объём в строке равен нулю (у Bitget биржа сама заполняет минуты без сделок плоскими свечами v=0; у Kraken/Lighter такие
               свечи при закачке НЕ записывались, у OKX биржа их не отдаёт — там «дыра» чаще значит «минута без сделок», а не потерю);
  фандинг    — интервал = мода разностей соседних time_ms (часы, округление до 0,01 ч) и мода колонки interval_h.
Ничего не выдумывается: если поля нет — пусто.
"""
import argparse, collections, csv, datetime, gzip, json, math, multiprocessing, os, sys, time, zlib

MIN_MS = 60_000
DAY_MS = 86_400_000
START_DEFAULT = "2026-10-01"
END_DEFAULT = "2026-11-05"

# сетки каталогов свечей по биржам: первая — основная (минутная; у Hyperliquid 15-минутная)
GRIDS = {"hyperliquid": [("candles15", 900_000), ("candles", 60_000)]}
GRIDS_DEFAULT = [("candles", 60_000)]
# основной контракт BTC (линейный, котируемый в USDT/USD) для минутного ряда оборота; --btc ex=SYMBOL,... меняет
BTC_DEFAULT = {"binance": "BTCUSDT", "bybit": "BTCUSDT", "okx": "BTC-USDT-SWAP", "bingx": "BTC-USDT", "aster": "BTCUSDT",
               "kraken": "PF_XBTUSD", "lighter": "BTC", "hyperliquid": "BTC", "bitget": "BTCUSDT"}
ALL_EXCHANGES = ["binance", "bybit", "okx", "bingx", "aster", "kraken", "lighter", "hyperliquid", "bitget"]

MANIFEST_FIELDS = [
    "exchange", "grid", "symbol", "base", "quote", "class", "status", "listed_ms", "delisted_ms", "in_instruments", "has_candles",
    "has_done", "done_note", "rows", "expected", "coverage", "full", "first_ms", "last_ms", "first_utc", "last_utc", "missing_head",
    "missing_tail", "gap_min", "gap_seg", "max_gap_min", "v0", "v0_share", "v_empty", "qv_empty", "bad_ohlc", "bad_nonpos", "bad_line",
    "dup", "unordered", "off_grid", "out_win", "consistent", "days_with_data", "days_full", "read_error",
    "fund_rows", "fund_first_ms", "fund_last_ms", "fund_iv_mode_h", "fund_iv_col_mode", "fund_n_iv", "fund_dup", "fund_unordered",
    "fund_bad", "fund_rate_min", "fund_rate_max", "fund_rate_mean"]


def parse_day(s):
    s = s.strip()
    return int(datetime.datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)


def utc(ms):
    if ms is None or ms == "":
        return ""
    return datetime.datetime.fromtimestamp(int(ms) / 1000, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")


def safe_name(sym):
    """Как в xdl_common.safe_name: имя файла символа."""
    return "".join(c if c.isalnum() or c in "-_.@" else "_" for c in sym)


# ---------------------------------------------------------------------------------------------------------------- один файл свечей
def scan_candles(path, step, start, end, mincnt, minv, series):
    """Построчный разбор файла свечей. mincnt/minv — общие накопители по сетке (число символов в шаг; число с v>0).
    series — None или словарь списков (ряд BTC). Возвращает словарь счётчиков."""
    nbars = (end - start) // step
    per_day = DAY_MS // step
    ndays = (end - start) // DAY_MS
    dayc = [0] * ndays
    rows = 0
    first = last = None
    gap_min = gap_seg = max_gap = 0
    v0 = v_empty = qv_empty = bad = bad_np = bad_line = dup = unordered = off_grid = out_win = 0
    prev = None
    err = ""
    try:
        with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
            f.readline()                                  # заголовок
            for line in f:
                p = line.rstrip("\r\n").split(",")
                if len(p) < 6:
                    if line.strip():
                        bad_line += 1
                    continue
                try:
                    t = int(p[0]); o = float(p[1]); h = float(p[2]); l = float(p[3]); c = float(p[4])
                    v = float(p[5]) if p[5] else None
                except ValueError:
                    bad_line += 1
                    continue
                if t % step:
                    off_grid += 1
                    continue
                if t < start or t >= end:
                    out_win += 1
                    continue
                if prev is not None:
                    if t == prev:
                        dup += 1
                        continue
                    if t < prev:
                        unordered += 1
                        continue
                    g = (t - prev) // step - 1
                    if g > 0:
                        gap_min += g
                        gap_seg += 1
                        if g > max_gap:
                            max_gap = g
                else:
                    first = t
                prev = t
                rows += 1
                if v is None:
                    v_empty += 1
                elif v == 0.0:
                    v0 += 1
                qs = p[6] if len(p) > 6 else ""
                if not qs:
                    qv_empty += 1
                if not (o > 0.0 and h > 0.0 and l > 0.0 and c > 0.0):
                    bad += 1
                    bad_np += 1
                elif h < (o if o > c else c) or l > (o if o < c else c):
                    bad += 1
                i = (t - start) // step
                mincnt[i] += 1
                if v is not None and v > 0.0:
                    minv[i] += 1
                dayc[i // per_day] += 1
                if series is not None:
                    series["o"][i] = p[1]; series["c"][i] = p[4]; series["v"][i] = p[5]; series["qv"][i] = qs
    except (EOFError, OSError, zlib.error) as e:
        err = "%s: %s" % (type(e).__name__, str(e)[:100])
    last = prev
    res = dict(rows=rows, expected=nbars, first=first, last=last, gap_min=gap_min, gap_seg=gap_seg, max_gap=max_gap, v0=v0,
               v_empty=v_empty, qv_empty=qv_empty, bad=bad, bad_np=bad_np, bad_line=bad_line, dup=dup, unordered=unordered,
               off_grid=off_grid, out_win=out_win, err=err, dayc=dayc, per_day=per_day)
    if rows:
        res["missing_head"] = (first - start) // step
        res["missing_tail"] = (end - step - last) // step
    else:
        res["missing_head"] = res["missing_tail"] = None
    return res


def scan_funding(path):
    out = dict(fund_rows=0, fund_first_ms="", fund_last_ms="", fund_iv_mode_h="", fund_iv_col_mode="", fund_n_iv="", fund_dup=0,
               fund_unordered=0, fund_bad=0, fund_rate_min="", fund_rate_max="", fund_rate_mean="")
    if not os.path.exists(path):
        return out
    times = []
    colv = collections.Counter()
    rmin = rmax = None
    rsum = 0.0
    nr = 0
    dup = unordered = bad = 0
    try:
        with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
            f.readline()
            for line in f:
                p = line.rstrip("\r\n").split(",")
                if len(p) < 2:
                    if line.strip():
                        bad += 1
                    continue
                try:
                    t = int(p[0]); r = float(p[1])
                except ValueError:
                    bad += 1
                    continue
                if times:
                    if t == times[-1]:
                        dup += 1
                        continue
                    if t < times[-1]:
                        unordered += 1
                        continue
                times.append(t)
                if len(p) > 2 and p[2]:
                    colv[p[2]] += 1
                if rmin is None or r < rmin:
                    rmin = r
                if rmax is None or r > rmax:
                    rmax = r
                rsum += r
                nr += 1
    except (EOFError, OSError, zlib.error):
        bad += 1
    out["fund_rows"] = len(times)
    out["fund_dup"], out["fund_unordered"], out["fund_bad"] = dup, unordered, bad
    if times:
        out["fund_first_ms"], out["fund_last_ms"] = times[0], times[-1]
        diffs = collections.Counter(round((b - a) / 3_600_000.0, 2) for a, b in zip(times, times[1:]))
        if diffs:
            out["fund_iv_mode_h"] = diffs.most_common(1)[0][0]
            out["fund_n_iv"] = len(diffs)
        if colv:
            out["fund_iv_col_mode"] = colv.most_common(1)[0][0]
        out["fund_rate_min"], out["fund_rate_max"] = repr(rmin), repr(rmax)
        out["fund_rate_mean"] = "%.10g" % (rsum / nr)
    return out


# ---------------------------------------------------------------------------------------------------------------- чанк файлов (процесс)
def scan_chunk(job):
    ex_dir, grid_dir, step, start, end, names, btc_name, with_funding = job
    nbars = (end - start) // step
    ndays = (end - start) // DAY_MS
    mincnt = [0] * nbars
    minv = [0] * nbars
    sym_active = [0] * ndays
    sym_full = [0] * ndays
    rows = {}
    series = None
    for name in names:
        want = (name == btc_name)
        s = {"o": [""] * nbars, "c": [""] * nbars, "v": [""] * nbars, "qv": [""] * nbars} if want else None
        r = scan_candles(os.path.join(ex_dir, grid_dir, name + ".csv.gz"), step, start, end, mincnt, minv, s)
        dayc, per_day = r.pop("dayc"), r.pop("per_day")
        r["days_with_data"] = sum(1 for x in dayc if x > 0)
        r["days_full"] = sum(1 for x in dayc if x == per_day)
        for d in range(ndays):
            if dayc[d] > 0:
                sym_active[d] += 1
            if dayc[d] == per_day:
                sym_full[d] += 1
        r["fund"] = scan_funding(os.path.join(ex_dir, "funding", name + ".csv.gz")) if with_funding else {}
        rows[name] = r
        if want:
            series = s
    return dict(rows=rows, mincnt=mincnt, minv=minv, sym_active=sym_active, sym_full=sym_full, series=series)


# ---------------------------------------------------------------------------------------------------------------- биржа целиком
def read_instruments(ex_dir):
    p = os.path.join(ex_dir, "instruments.csv")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def scan_exchange(a, ex, grid_dir, step, primary):
    t0 = time.time()
    ex_dir = os.path.join(a.root, ex)
    start, end = parse_day(a.start), parse_day(a.end)
    nbars = (end - start) // step
    ndays = (end - start) // DAY_MS
    instr = read_instruments(ex_dir)
    by_file = {safe_name(r["symbol"]): r for r in instr}
    cdir = os.path.join(ex_dir, grid_dir)
    names = sorted(fn[:-7] for fn in os.listdir(cdir) if fn.endswith(".csv.gz")) if os.path.isdir(cdir) else []
    btc_name = safe_name(a.btc_map.get(ex, BTC_DEFAULT.get(ex, "")))
    if btc_name not in names:
        btc_name = ""
    sdir = os.path.join(ex_dir, "_state")
    done = {fn[:-5] for fn in os.listdir(sdir) if fn.endswith(".done")} if os.path.isdir(sdir) else set()
    # распределение по процессам: крупные файлы вперёд, раунд-робин
    sizes = {n: os.path.getsize(os.path.join(cdir, n + ".csv.gz")) for n in names}
    order = sorted(names, key=lambda n: -sizes[n])
    nproc = max(1, min(a.procs, len(order)))
    chunks = [order[i::nproc] for i in range(nproc)]
    jobs = [(ex_dir, grid_dir, step, start, end, c, btc_name, primary) for c in chunks if c]
    if nproc > 1:
        with multiprocessing.Pool(nproc) as pool:
            results = pool.map(scan_chunk, jobs)
    else:
        results = [scan_chunk(j) for j in jobs]
    rows = {}
    mincnt = [0] * nbars
    minv = [0] * nbars
    sym_active = [0] * ndays
    sym_full = [0] * ndays
    series = None
    for r in results:
        rows.update(r["rows"])
        for i, x in enumerate(r["mincnt"]):
            mincnt[i] += x
        for i, x in enumerate(r["minv"]):
            minv[i] += x
        for d in range(ndays):
            sym_active[d] += r["sym_active"][d]
            sym_full[d] += r["sym_full"][d]
        if r["series"] is not None:
            series = r["series"]
    # манифест: все файлы + инструменты без файла
    tag = ex if primary else "%s__%s" % (ex, grid_dir)
    suffix = ("." + a.tag) if a.tag else ""
    man = []
    seen = set()
    for n in names:
        seen.add(n)
        man.append(manifest_row(ex, grid_dir, n, by_file.get(n), rows[n], n in done, sdir, start, end, step, nbars))
    if primary:
        for fn, ins in by_file.items():
            if fn not in seen:
                man.append(manifest_row(ex, grid_dir, fn, ins, None, fn in done, sdir, start, end, step, nbars))
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "manifest_%s%s.csv" % (tag, suffix)), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        w.writeheader()
        for m in man:
            w.writerow(m)
    per_day = DAY_MS // step
    n_files = len(names)
    with open(os.path.join(a.out, "daily_%s%s.csv" % (tag, suffix)), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["day", "n_files", "n_sym_active", "n_sym_full", "minutes_present", "bars_per_day", "cover_active", "cover_files"])
        for d in range(ndays):
            present = sum(mincnt[d * per_day:(d + 1) * per_day])
            w.writerow([utc(start + d * DAY_MS)[:10], n_files, sym_active[d], sym_full[d], present, per_day,
                        "%.6f" % (present / (sym_active[d] * per_day)) if sym_active[d] else "",
                        "%.6f" % (present / (n_files * per_day)) if n_files else ""])
    with open(os.path.join(a.out, "minutes_%s%s.csv" % (tag, suffix)), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_ms", "n_rows", "n_vpos", "btc_o", "btc_c", "btc_v", "btc_qv"])
        for i in range(nbars):
            if series is not None:
                w.writerow([start + i * step, mincnt[i], minv[i], series["o"][i], series["c"][i], series["v"][i], series["qv"][i]])
            else:
                w.writerow([start + i * step, mincnt[i], minv[i], "", "", "", ""])
    meta = dict(exchange=ex, grid=grid_dir, step_ms=step, tag=a.tag, window=[a.start, a.end], nbars=nbars, n_instruments=len(instr),
                n_candle_files=n_files, n_done=len(done), btc_symbol=btc_name, seconds=round(time.time() - t0, 1), python=sys.version.split()[0],
                host=os.uname().nodename if hasattr(os, "uname") else "", scanned_utc=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M"))
    with open(os.path.join(a.out, "meta_%s%s.json" % (tag, suffix)), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print("[%s/%s] файлов %d, инструментов %d, done %d, BTC=%s, %.0f с" % (ex, grid_dir, n_files, len(instr), len(done), btc_name or "—", time.time() - t0), flush=True)


def manifest_row(ex, grid_dir, name, ins, r, is_done, sdir, start, end, step, nbars):
    m = {k: "" for k in MANIFEST_FIELDS}
    m.update(exchange=ex, grid=grid_dir, symbol=(ins or {}).get("symbol", name), in_instruments=1 if ins else 0,
             has_done=1 if is_done else 0, expected=nbars)
    if ins:
        for k_src, k in (("base", "base"), ("quote", "quote"), ("class", "class"), ("status", "status"),
                         ("listed_ms", "listed_ms"), ("delisted_ms", "delisted_ms")):
            m[k] = ins.get(k_src, "")
    if is_done:
        try:
            with open(os.path.join(sdir, name + ".done"), encoding="utf-8", errors="replace") as f:
                m["done_note"] = f.read(120).replace("\n", " ").replace(",", ";")
        except OSError:
            pass
    if r is None:
        m["has_candles"] = 0
        m["rows"] = 0
        return m
    m["has_candles"] = 1
    m["rows"] = r["rows"]
    m["coverage"] = "%.6f" % (r["rows"] / nbars)
    m["full"] = 1 if r["rows"] == nbars else 0
    m["first_ms"], m["last_ms"] = ("" if r["first"] is None else r["first"]), ("" if r["last"] is None else r["last"])
    m["first_utc"], m["last_utc"] = utc(r["first"]), utc(r["last"])
    m["missing_head"], m["missing_tail"] = ("" if r["missing_head"] is None else r["missing_head"]), ("" if r["missing_tail"] is None else r["missing_tail"])
    m["gap_min"], m["gap_seg"], m["max_gap_min"] = r["gap_min"], r["gap_seg"], r["max_gap"]
    m["v0"] = r["v0"]
    m["v0_share"] = "%.6f" % (r["v0"] / r["rows"]) if r["rows"] else ""
    m["v_empty"], m["qv_empty"] = r["v_empty"], r["qv_empty"]
    m["bad_ohlc"], m["bad_nonpos"], m["bad_line"] = r["bad"], r["bad_np"], r["bad_line"]
    m["dup"], m["unordered"], m["off_grid"], m["out_win"] = r["dup"], r["unordered"], r["off_grid"], r["out_win"]
    if r["rows"]:
        m["consistent"] = 1 if r["rows"] + r["gap_min"] + r["missing_head"] + r["missing_tail"] == nbars else 0
    m["days_with_data"], m["days_full"] = r["days_with_data"], r["days_full"]
    m["read_error"] = r["err"]
    m.update(r["fund"])
    return m


def cmd_scan(a):
    a.btc_map = dict(kv.split("=", 1) for kv in a.btc.split(",") if "=" in kv) if a.btc else {}
    exs = [e for e in a.exchanges.split(",") if e]
    for ex in exs:
        grids = GRIDS.get(ex, GRIDS_DEFAULT)
        for gi, (gd, st) in enumerate(grids):
            if not os.path.isdir(os.path.join(a.root, ex, gd)):
                print("[%s] нет каталога %s — пропуск" % (ex, gd), flush=True)
                continue
            scan_exchange(a, ex, gd, st, gi == 0)


# ---------------------------------------------------------------------------------------------------------------- merge
def read_csv(p):
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def cmd_merge(a):
    ex = a.exchange
    parts = [x for x in a.parts.split(",") if x]
    mans, dailies, minutes, metas = [], [], [], []
    for t in parts:
        mans.append(read_csv(os.path.join(a.out, "manifest_%s.%s.csv" % (ex, t))))
        dailies.append(read_csv(os.path.join(a.out, "daily_%s.%s.csv" % (ex, t))))
        minutes.append(read_csv(os.path.join(a.out, "minutes_%s.%s.csv" % (ex, t))))
        with open(os.path.join(a.out, "meta_%s.%s.json" % (ex, t)), encoding="utf-8") as f:
            metas.append(json.load(f))
    best = {}
    for rows in mans:
        for r in rows:
            k = r["symbol"]
            if k not in best or (r["has_candles"] == "1" and best[k]["has_candles"] != "1"):
                best[k] = r
    with open(os.path.join(a.out, "manifest_%s.csv" % ex), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        w.writeheader()
        for r in best.values():
            w.writerow(r)
    ndays = len(dailies[0])
    with open(os.path.join(a.out, "daily_%s.csv" % ex), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["day", "n_files", "n_sym_active", "n_sym_full", "minutes_present", "bars_per_day", "cover_active", "cover_files"])
        for d in range(ndays):
            rs = [x[d] for x in dailies]
            nf = sum(int(x["n_files"]) for x in rs)
            na = sum(int(x["n_sym_active"]) for x in rs)
            nfull = sum(int(x["n_sym_full"]) for x in rs)
            pres = sum(int(x["minutes_present"]) for x in rs)
            bpd = int(rs[0]["bars_per_day"])
            w.writerow([rs[0]["day"], nf, na, nfull, pres, bpd, "%.6f" % (pres / (na * bpd)) if na else "", "%.6f" % (pres / (nf * bpd)) if nf else ""])
    with open(os.path.join(a.out, "minutes_%s.csv" % ex), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_ms", "n_rows", "n_vpos", "btc_o", "btc_c", "btc_v", "btc_qv"])
        for i in range(len(minutes[0])):
            rs = [x[i] for x in minutes]
            btc = next((x for x in rs if x["btc_c"] != ""), rs[0])
            w.writerow([rs[0]["t_ms"], sum(int(x["n_rows"]) for x in rs), sum(int(x["n_vpos"]) for x in rs), btc["btc_o"], btc["btc_c"], btc["btc_v"], btc["btc_qv"]])
    meta = dict(metas[0])
    meta.update(tag="merged:" + "+".join(parts), n_candle_files=sum(m["n_candle_files"] for m in metas), n_done=sum(m["n_done"] for m in metas),
                n_instruments=max(m["n_instruments"] for m in metas), parts=metas, btc_symbol=next((m["btc_symbol"] for m in metas if m["btc_symbol"]), ""))
    with open(os.path.join(a.out, "meta_%s.json" % ex), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print("[merge %s] частей %d: файлов %d, done %d из %d инструментов" % (ex, len(parts), meta["n_candle_files"], meta["n_done"], meta["n_instruments"]))


# ---------------------------------------------------------------------------------------------------------------- report (ПК)
def fnum(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])


def runs(flags, min_len):
    """Подряд идущие True: список (начало, длина) длиной >= min_len."""
    out, i, n = [], 0, len(flags)
    while i < n:
        if flags[i]:
            j = i
            while j < n and flags[j]:
                j += 1
            if j - i >= min_len:
                out.append((i, j - i))
            i = j
        else:
            i += 1
    return out


def cmd_report(a):
    exs = []
    for ex in ALL_EXCHANGES:
        if os.path.exists(os.path.join(a.inp, "manifest_%s.csv" % ex)):
            exs.append(ex)
    allrows = []
    summ = []
    cls_rows = []
    daily_cols = {}
    outages = []
    btc_daily = {}
    for ex in exs:
        man = read_csv(os.path.join(a.inp, "manifest_%s.csv" % ex))
        with open(os.path.join(a.inp, "meta_%s.json" % ex), encoding="utf-8") as f:
            meta = json.load(f)
        allrows.extend(man)
        have = [r for r in man if r["has_candles"] == "1"]
        with_rows = [r for r in have if int(r["rows"]) > 0]
        nb = int(meta["nbars"])
        rows_tot = sum(int(r["rows"]) for r in with_rows)
        v0_tot = sum(int(r["v0"]) for r in with_rows)
        gap_tot = sum(int(r["gap_min"]) for r in with_rows)
        full = [r for r in with_rows if r["full"] == "1"]
        cov = sorted(fnum(r["coverage"]) for r in with_rows)
        bad_syms = [r for r in with_rows if int(r["bad_ohlc"]) > 0]
        fund_files = [r for r in man if int(r["fund_rows"] or 0) > 0]
        iv = collections.Counter(r["fund_iv_mode_h"] for r in fund_files)
        status_nocandle = collections.Counter(r["status"] for r in man if r["has_candles"] != "1")
        status_all = collections.Counter(r["status"] for r in man if r["in_instruments"] == "1")
        t_start = parse_day(meta["window"][0])
        t_end = parse_day(meta["window"][1])
        n_head_listed = n_head_other = n_tail_delisted = n_tail_other = n_gaps = 0
        for r in with_rows:
            lm = int(r["listed_ms"]) if r["listed_ms"] else None
            dl = int(r["delisted_ms"]) if r["delisted_ms"] else None
            if int(r["missing_head"]) > 0:
                if lm is not None and lm >= t_start:
                    n_head_listed += 1
                else:
                    n_head_other += 1
            if int(r["missing_tail"]) > 0:
                if dl is not None and dl < t_end:
                    n_tail_delisted += 1
                else:
                    n_tail_other += 1
            if int(r["gap_min"]) > 0:
                n_gaps += 1
        s = dict(exchange=ex, grid=meta["grid"], bars=nb, instruments=sum(1 for r in man if r["in_instruments"] == "1"), with_file=len(have), with_rows=len(with_rows), done=meta["n_done"],
                 full=len(full), cov_min=cov[0] if cov else "", cov_p10=cov[len(cov) // 10] if cov else "", cov_median=median(cov) if cov else "",
                 rows_total=rows_tot, v0_share=(v0_tot / rows_tot) if rows_tot else "", gap_min_total=gap_tot,
                 syms_with_gaps=sum(1 for r in with_rows if int(r["gap_min"]) > 0), max_gap_min=max([int(r["max_gap_min"]) for r in with_rows] or [0]),
                 bad_ohlc_total=sum(int(r["bad_ohlc"]) for r in with_rows), bad_ohlc_syms=len(bad_syms),
                 dup=sum(int(r["dup"]) for r in with_rows), unordered=sum(int(r["unordered"]) for r in with_rows),
                 off_grid=sum(int(r["off_grid"]) for r in with_rows), out_win=sum(int(r["out_win"]) for r in with_rows),
                 inconsistent=sum(1 for r in with_rows if r["consistent"] == "0"), read_errors=sum(1 for r in with_rows if r["read_error"]),
                 qv_empty_share=(sum(int(r["qv_empty"]) for r in with_rows) / rows_tot) if rows_tot else "",
                 fund_files=len(fund_files), fund_rows=sum(int(r["fund_rows"]) for r in fund_files),
                 fund_iv_modes=json.dumps(dict(iv.most_common()), ensure_ascii=False), no_candle_status=json.dumps(dict(status_nocandle), ensure_ascii=False),
                 status_all=json.dumps(dict(status_all.most_common()), ensure_ascii=False), head_listed_in_window=n_head_listed,
                 head_other=n_head_other, tail_delisted_in_window=n_tail_delisted, tail_other=n_tail_other, with_gaps=n_gaps)
        summ.append(s)
        # по классам
        byc = collections.defaultdict(list)
        for r in with_rows:
            byc[r["class"] or "?"].append(r)
        inst_c = collections.Counter(r["class"] or "?" for r in man)
        for c in sorted(inst_c):
            rs = byc.get(c, [])
            rt = sum(int(r["rows"]) for r in rs)
            cls_rows.append(dict(exchange=ex, cls=c, instruments=inst_c[c], with_rows=len(rs), full=sum(1 for r in rs if r["full"] == "1"),
                                 rows=rt, coverage_of_expected=(rt / (len(rs) * nb)) if rs else "",
                                 v0_share=(sum(int(r["v0"]) for r in rs) / rt) if rt else "", gap_min=sum(int(r["gap_min"]) for r in rs),
                                 bad_ohlc=sum(int(r["bad_ohlc"]) for r in rs)))
        # суточное покрытие
        dly = read_csv(os.path.join(a.inp, "daily_%s.csv" % ex))
        daily_cols[ex] = dly
        # выпадения биржи целиком: минутный ряд числа символов и BTC
        mins = read_csv(os.path.join(a.inp, "minutes_%s.csv" % ex))
        step = int(meta["step_ms"])
        nrows = [int(x["n_rows"]) for x in mins]
        med = median(nrows) or 0
        flags0 = [x == 0 for x in nrows]
        flagsl = [x < 0.5 * med for x in nrows]
        for kind, fl in (("n_rows=0", flags0), ("n_rows<50% медианы", flagsl)):
            for i, ln in runs(fl, 1 if kind == "n_rows=0" else 5):
                outages.append(dict(exchange=ex, kind=kind, start=utc(int(mins[i]["t_ms"])), minutes=ln * step // MIN_MS, median_n=med,
                                    min_n=min(nrows[i:i + ln])))
        # ряд оборота BTC: quote_v, если биржа отдаёт, иначе оценка v * close (помечена в колонке src)
        tov = []
        for x in mins:
            if x["btc_c"] == "":
                tov.append(0.0)
            elif x["btc_qv"] != "":
                tov.append(fnum(x["btc_qv"]))
            else:
                tov.append(fnum(x["btc_v"]) * fnum(x["btc_c"]))
        per_day_m = 86_400_000 // step
        day_tov = [sum(tov[d * per_day_m:(d + 1) * per_day_m]) for d in range(len(tov) // per_day_m)]
        btc_daily[ex] = (day_tov, "quote_v" if any(x["btc_qv"] != "" for x in mins) else "v*close")
        btc_miss = [x["btc_c"] == "" for x in mins]
        if meta.get("btc_symbol"):
            for i, ln in runs(btc_miss, 1):
                outages.append(dict(exchange=ex, kind="BTC нет свечи", start=utc(int(mins[i]["t_ms"])), minutes=ln * step // MIN_MS, median_n=med, min_n=0))
            btc_v0 = [x["btc_v"] != "" and fnum(x["btc_v"], 1.0) == 0.0 for x in mins]
            for i, ln in runs(btc_v0, 1):
                outages.append(dict(exchange=ex, kind="BTC v=0", start=utc(int(mins[i]["t_ms"])), minutes=ln * step // MIN_MS, median_n=med, min_n=0))
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "manifest.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        w.writeheader()
        for r in allrows:
            w.writerow(r)
    if summ:
        with open(os.path.join(a.out, "exchange_summary.csv"), "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(summ[0].keys()))
            w.writeheader()
            for s in summ:
                w.writerow(s)
    if cls_rows:
        with open(os.path.join(a.out, "class_summary.csv"), "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(cls_rows[0].keys()))
            w.writeheader()
            for s in cls_rows:
                w.writerow(s)
    # суточный оборот BTC по биржам (USD; источник — quote_v либо оценка v*close) и скрининг дней (см. ниже)
    if btc_daily:
        days_l = [r["day"] for r in next(iter(daily_cols.values()))]
        with open(os.path.join(a.out, "btc_daily_turnover.csv"), "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["day"] + ["%s:%s" % (e, btc_daily[e][1]) for e in btc_daily])
            for d, day in enumerate(days_l):
                w.writerow([day] + ["%.0f" % btc_daily[e][0][d] for e in btc_daily])
        # скрининг по сверстникам (выходные у всех бирж просаживают оборот одинаково): день биржи e отмечается, если её оборот дня,
        # делённый на её медиану дня, ниже 50 % медианы такого же отношения по остальным биржам в тот же день
        norm = {e: [v / median(dt) if median(dt) else 0.0 for v in dt] for e, (dt, src) in btc_daily.items()}
        for e, (dt, src) in btc_daily.items():
            for d in range(len(dt)):
                peers = [norm[x][d] for x in norm if x != e]
                if peers and norm[e][d] < 0.5 * median(peers):
                    outages.append(dict(exchange=e, kind="BTC оборот дня < 50%% от сверстников (%s)" % src, start=days_l[d], minutes=1440,
                                        median_n=round(median(dt)), min_n=round(dt[d])))
    with open(os.path.join(a.out, "outages.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["exchange", "kind", "start", "minutes", "median_n", "min_n"])
        w.writeheader()
        for o in outages:
            w.writerow(o)
    # матрица суточного покрытия: cover_active по биржам
    if daily_cols:
        days = [r["day"] for r in next(iter(daily_cols.values()))]
        with open(os.path.join(a.out, "daily_matrix.csv"), "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["day"] + [e + ":cover_active" for e in daily_cols] + [e + ":n_sym_active" for e in daily_cols])
            for d, day in enumerate(days):
                w.writerow([day] + [daily_cols[e][d]["cover_active"] for e in daily_cols] + [daily_cols[e][d]["n_sym_active"] for e in daily_cols])
    print("бирж %d: %s" % (len(exs), ", ".join(exs)))
    for s in summ:
        print(json.dumps(s, ensure_ascii=False))
    print("выпадения/скрининг: %d строк" % len(outages))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--root", required=True, help="каталог с <биржа>/{candles,funding,instruments.csv,_state}")
    s.add_argument("--out", required=True)
    s.add_argument("--exchanges", default=",".join(ALL_EXCHANGES))
    s.add_argument("--start", default=START_DEFAULT)
    s.add_argument("--end", default=END_DEFAULT)
    s.add_argument("--procs", type=int, default=1, help="процессов на биржу (Sofia — 1, по правилу В-182)")
    s.add_argument("--tag", default="", help="суффикс частей одной биржи на разных машинах (A/B)")
    s.add_argument("--btc", default="", help="ex=SYMBOL,… — основной контракт BTC вместо умолчания")
    m = sub.add_parser("merge")
    m.add_argument("--out", required=True)
    m.add_argument("--exchange", required=True)
    m.add_argument("--parts", required=True, help="теги частей через запятую, например A,B")
    r = sub.add_parser("report")
    r.add_argument("--in", dest="inp", required=True)
    r.add_argument("--out", required=True)
    a = ap.parse_args()
    {"scan": cmd_scan, "merge": cmd_merge, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    main()
