#!/usr/bin/env python3
"""TK-493 F7 шаг 2 (Steam Deck, только stdlib): панели с маской кассы США, панели формирования (обрезаны по 15.10 00:00),
сдвинутые на +1 сутки (для эмпирического нуля A1), каталоги символьными ссылками.

  python3 sel_deck_prep.py mask   - ~/xdl9/panels_us/<биржа>/<SYM>.sap: маска кассы США на всех 35 сутках (us_mask=1)
  python3 sel_deck_prep.py form   - ~/xdl9/form/<биржа>/<SYM>.sap:
                                    ТОЛЬКО минуты формирования [01.10 00:00, 15.10 00:00); торговое окно физически не попадает в файл
  python3 sel_deck_prep.py formsh - ~/xdl9/form_sh/<тег>/<биржа>/<SYM>.sap, тег p1/p2/m1 = сдвиг партнёра на +1, +2, -1 (П-37 r3.1 §7.2) для контрактов
                                    каталогов A: крипта - календарные сутки, остальные полосы - торговые дни (sel_shift.py); панель содержит только
                                    общее пересечение сдвигов 03.10-13.10 (сутки 2467-2477, 15 840 минут), источники лежат внутри формирования
  python3 sel_deck_prep.py fundcut- ~/xdl9/funding_form/<биржа>/<SYM>.csv.gz: фандинг ТОЛЬКО формирования (time_ms < 15.10 00:00 UTC). Механическая
                                    обрезка по метке времени: у каждой строки разбирается только первое поле, остановка на первой строке >= границы;
                                    значения ставок торгового окна не разбираются и не читаются дальше (D2 и все расчёты S1 берут только эти файлы)
  python3 sel_deck_prep.py links  - ~/xdl9/trade/<биржа>/ (все контракты теста), ~/xdl9/cat/{a,c}/<биржа>-<полоса>/,
                                    ~/xdl9/cat/form/<биржа>-<полоса>/{real, null<k><тег>}/ (k = 1..4 разбиение, тег - сдвиг)
Вход: ~/xdl9/sel/contracts.csv, ~/xdl9/sel/splits.csv (с ПК). Формат .sap: panel.rs (SAP1, u32 резерв, i64 first_min, u64 n, open[n], close[n]).
Календарь (П-37 §9, Н22): касса 13:30-20:00 UTC в будни до 31.10 включительно (EDT), 14:30-21:00 UTC с 01.11 (EST).
"""
import array, csv, os, struct, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HOME = os.path.expanduser("~/xdl9")
GRID0_DAY = 0                      # сутки от 2020-01-01
D_FORM0 = 2465                     # 01.10
D_TRADE0 = 2479                    # 15.10
D_DST_END = 2496                   # 01.11 (воскресенье): с этих суток зимнее время
MIN_DAY = 1440
NAN = float("nan")


def safe(sym):
    return "".join(c if c.isalnum() or c in "-_.@" else "_" for c in sym)


def read_sap(path):
    with open(path, "rb") as f:
        h = f.read(24)
        if h[:4] != b"SAP1":
            raise ValueError("не SAP1: " + path)
        first_min, n = struct.unpack("<qQ", h[8:24])
        op = array.array("d")
        op.fromfile(f, n)
        cl = array.array("d")
        cl.fromfile(f, n)
    if sys.byteorder != "little":
        op.byteswap(); cl.byteswap()
    return first_min, op, cl


def write_sap(path, first_min, op, cl):
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(b"SAP1" + struct.pack("<I", 0) + struct.pack("<qQ", first_min, len(cl)))
        for col in (op, cl):
            if sys.byteorder != "little":
                col = array.array("d", col); col.byteswap()
            col.tofile(f)
    os.replace(tmp, path)


def out_of_session_runs(m0, m1):
    """Отрезки [a, b) минут (абсолютных) в [m0, m1), НЕ входящие в кассу США."""
    runs = []
    cur = m0
    d0, d1 = m0 // MIN_DAY, (m1 - 1) // MIN_DAY
    for d in range(d0, d1 + 1):
        dow = (d + 2) % 7           # 2020-01-01 - среда (python weekday 2)
        if dow >= 5:
            continue
        open_m, close_m = (13 * 60 + 30, 20 * 60) if d < D_DST_END else (14 * 60 + 30, 21 * 60)
        s, e = max(d * MIN_DAY + open_m, m0), min(d * MIN_DAY + close_m, m1)
        if e <= s:
            continue
        if s > cur:
            runs.append((cur, s))
        cur = max(cur, e)
    if cur < m1:
        runs.append((cur, m1))
    return runs


def mask_arrays(first_min, op, cl):
    n = len(cl)
    for a, b in out_of_session_runs(first_min, first_min + n):
        fill = array.array("d", [NAN]) * (b - a)
        op[a - first_min:b - first_min] = fill
        cl[a - first_min:b - first_min] = fill


def load_contracts():
    with open(os.path.join(HOME, "sel", "contracts.csv"), encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def cmd_mask():
    t0 = time.time()
    n = miss = 0
    for r in load_contracts():
        if r["us_mask"] != "1":
            continue
        src = os.path.join(HOME, "panels", r["exchange"], safe(r["symbol"]) + ".sap")
        if not os.path.exists(src):
            miss += 1
            continue
        dst_dir = os.path.join(HOME, "panels_us", r["exchange"])
        os.makedirs(dst_dir, exist_ok=True)
        fm, op, cl = read_sap(src)
        mask_arrays(fm, op, cl)
        write_sap(os.path.join(dst_dir, os.path.basename(src)), fm, op, cl)
        n += 1
    print("mask: записано %d, нет панели %d, %.1f с" % (n, miss, time.time() - t0))


def trade_src(r):
    base = "panels_us" if r["us_mask"] == "1" else "panels"
    return os.path.join(HOME, base, r["exchange"], safe(r["symbol"]) + ".sap")


def cmd_form():
    t0 = time.time()
    n = miss = 0
    cut = D_TRADE0 * MIN_DAY
    for r in load_contracts():
        src = trade_src(r)
        if not os.path.exists(src):
            miss += 1
            continue
        fm, op, cl = read_sap(src)
        keep = max(0, min(len(cl), cut - fm))
        op, cl = op[:keep], cl[:keep]
        for sub, shift in (("form", 0),):
            d = os.path.join(HOME, sub, r["exchange"])
            os.makedirs(d, exist_ok=True)
            if keep > 0:
                write_sap(os.path.join(d, os.path.basename(src)), fm + shift, op, cl)
        n += 1
    print("form: контрактов %d, нет панели %d, %.1f с" % (n, miss, time.time() - t0))


def abs_slice(col, first_min, start, n):
    """n значений ряда col (начало на абсолютной минуте first_min) с абсолютной минуты start; вне панели - NaN."""
    out = array.array("d", [NAN]) * n
    lo, hi = max(start, first_min), min(start + n, first_min + len(col))
    if hi > lo:
        out[lo - start:hi - start] = col[lo - first_min:hi - first_min]
    return out


def cmd_formsh():
    import sel_shift as SH
    t0 = time.time()
    first, end = SH.common_window("cal")
    nd = end - first
    assert SH.common_window("trade") == (first, end)
    have = set()
    with open(os.path.join(HOME, "sel", "splits.csv"), encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            have.add((r["exchange"], r["symbol"], r["slug"]))
    n = 0
    for ex, sym, slug in sorted(have):
        mode = SH.mode_of(slug)
        src = os.path.join(HOME, "form", ex, safe(sym) + ".sap")
        if not os.path.exists(src):
            continue
        fm, op, cl = read_sap(src)
        for k in SH.SHIFTS:
            out = [array.array("d", [NAN]) * (nd * MIN_DAY), array.array("d", [NAN]) * (nd * MIN_DAY)]
            for d in range(first, end):
                sd = SH.source_day(d, k, mode)
                assert sd is not None and D_FORM0 <= sd < D_TRADE0, (d, k, mode, sd)
                for c, col in enumerate((op, cl)):
                    out[c][(d - first) * MIN_DAY:(d - first + 1) * MIN_DAY] = abs_slice(col, fm, sd * MIN_DAY, MIN_DAY)
            dd = os.path.join(HOME, "form_sh", SH.TAG[k], ex)
            os.makedirs(dd, exist_ok=True)
            write_sap(os.path.join(dd, os.path.basename(src)), first * MIN_DAY, out[0], out[1])
        n += 1
    print("formsh: контрактов %d x %d сдвигов, окно суток [%d, %d), %.1f с" % (n, len(SH.SHIFTS), first, end, time.time() - t0))


def cmd_fundcut():
    import gzip
    t0 = time.time()
    cut_ms = 1_577_836_800_000 + D_TRADE0 * MIN_DAY * 60_000      # 15.10 00:00 UTC (начало сетки панелей 2020-01-01 + сутки)
    n = rows = 0
    for ex in sorted(os.listdir(os.path.join(HOME, "funding"))):
        d_in = os.path.join(HOME, "funding", ex)
        if not os.path.isdir(d_in):
            continue
        d_out = os.path.join(HOME, "funding_form", ex)
        os.makedirs(d_out, exist_ok=True)
        for fn in sorted(os.listdir(d_in)):
            if not fn.endswith(".csv.gz"):
                continue
            tmp = os.path.join(d_out, fn + ".part")
            with gzip.open(os.path.join(d_in, fn), "rt", encoding="utf-8", newline="") as fi, gzip.open(tmp, "wt", encoding="utf-8", newline="") as fo:
                fo.write(fi.readline())                              # заголовок
                for line in fi:
                    if int(line.split(",", 1)[0]) >= cut_ms:        # разбирается только метка времени; дальше строка не используется
                        break
                    fo.write(line)
                    rows += 1
            os.replace(tmp, os.path.join(d_out, fn))
            n += 1
    print("fundcut: файлов %d, строк формирования %d, %.1f с" % (n, rows, time.time() - t0))


def link(target, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.lexists(path):
        os.remove(path)
    os.symlink(target, path)


def cmd_links():
    import sel_shift as SH
    t0 = time.time()
    rows = load_contracts()
    cnt = {"trade": 0, "a": 0, "c": 0, "form": 0}
    for r in rows:
        stem = safe(r["symbol"]) + ".sap"
        tgt = trade_src(r)
        if not os.path.exists(tgt):
            continue
        link(tgt, os.path.join(HOME, "trade", r["exchange"], stem)); cnt["trade"] += 1
        cid = r["exchange"] + "-" + r["slug"]
        if r["in_catalog_a"] == "1":
            link(tgt, os.path.join(HOME, "cat", "a", cid, stem)); cnt["a"] += 1
            link(os.path.join(HOME, "form", r["exchange"], stem), os.path.join(HOME, "cat", "form", cid, "real", stem))
        if r["leg_c1d2"] == "1":
            link(tgt, os.path.join(HOME, "cat", "c", cid, stem)); cnt["c"] += 1
    with open(os.path.join(HOME, "sel", "splits.csv"), encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            stem = safe(r["symbol"]) + ".sap"
            for k in SH.SHIFTS:
                sub = os.path.join(HOME, "form", r["exchange"]) if r["half"] == "A" else os.path.join(HOME, "form_sh", SH.TAG[k], r["exchange"])
                link(os.path.join(sub, stem), os.path.join(HOME, "cat", "form", r["catalog"], "null" + r["split"] + SH.TAG[k], stem))
                cnt["form"] += 1
    print("links:", cnt, "%.1f с" % (time.time() - t0))


if __name__ == "__main__":
    {"mask": cmd_mask, "form": cmd_form, "formsh": cmd_formsh, "fundcut": cmd_fundcut, "links": cmd_links}[sys.argv[1]]()
