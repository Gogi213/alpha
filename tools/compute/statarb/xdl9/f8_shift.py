#!/usr/bin/env python3
"""TK-493 S3 (П-37 r3.1 §11.1-11.2): сдвиг партнёра на +1 сутки по всему окну 35 суток для плацебо F8. Только stdlib (Steam Deck и ПК).

Единица сдвига - как в нуле A1 (sel_shift.py, П-37 §7.2): крипта - календарные сутки, остальные полосы - торговые дни (суббота и воскресенье берут
ближайшую пятницу). Правило обобщено с формирования (сутки 2465-2478) на окно 35 суток (2465-2499): для формирования результат совпадает с
sel_shift.source_day(d, 1, mode) (проверяется тестом). Цель d берёт содержимое суток-источника целиком (1440 минут того же времени суток).
Нет источника (первые сутки окна) - NaN; у фандинга - строк нет.

  python3 f8_shift.py mkplac --needs <tsv> --src ~/xdl9/trade --fsrc ~/xdl9/funding --out ~/xdl9/plac [--lo 2465 --hi 2500] [--k 1] [--dry]
      needs - tsv `layout<TAB>exchange<TAB>symbol<TAB>mode` (layout p - `plac/p/<ex>/<SYM>@p1.sap` рядом с настоящими панелями биржи;
      x - `plac/x/<ex>/<SYM>.sap` под тем же именем, для --panels-b/--funding-x cross и funding-*). Панели и фандинг только пишутся в --out,
      сутки читаются целыми блоками (механический перенос, без разбора значений); числа не печатаются.
  python3 f8_shift.py selftest
"""
import array, csv, gzip, os, struct, sys

D_FORM0, D_TRADE0, D_END = 2465, 2479, 2500      # 01.10, 15.10, 05.11 (не включая)
MIN_DAY = 1440
GRID0_MS = 1_577_836_800_000                     # 2020-01-01 00:00 UTC
NAN = float("nan")
TAG = "@p1"


def is_weekday(d):
    """2020-01-01 - среда: weekday = (d + 2) % 7, 0 = понедельник."""
    return (d + 2) % 7 < 5


def source_day(d, k, mode, lo=D_FORM0, hi=D_END):
    """Сутки-источник для цели d при сдвиге партнёра на +k (партнёр взят из прошлого) в окне [lo, hi) или None."""
    if not lo <= d < hi:
        return None
    if mode == "cal":
        s = d - k
        return s if lo <= s < hi else None
    if mode == "trade":
        wd = [w for w in range(lo, hi) if is_weekday(w)]
        j = sum(1 for w in wd if w < d) - k
        return wd[j] if 0 <= j < len(wd) else None
    raise ValueError("mode: cal | trade")


def day_map(k, mode, lo=D_FORM0, hi=D_END):
    """{цель: источник} для целей с источником."""
    out = {}
    for d in range(lo, hi):
        s = source_day(d, k, mode, lo, hi)
        if s is not None:
            out[d] = s
    return out


def mode_of_slug(slug):
    return "cal" if slug == "crypto" else "trade"


# ------------------------------------------------------------------ панели .sap (формат panel.rs: SAP1, u32 резерв, i64 first_min, u64 n, open[n], close[n])
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
        op.byteswap()
        cl.byteswap()
    return first_min, op, cl


def write_sap(path, first_min, op, cl):
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(b"SAP1" + struct.pack("<I", 0) + struct.pack("<qQ", first_min, len(cl)))
        for col in (op, cl):
            if sys.byteorder != "little":
                col = array.array("d", col)
                col.byteswap()
            col.tofile(f)
    os.replace(tmp, path)


def abs_slice(col, first_min, start, n):
    """n значений ряда col (начало - абсолютная минута first_min) с абсолютной минуты start; вне панели - NaN."""
    out = array.array("d", [NAN]) * n
    lo, hi = max(start, first_min), min(start + n, first_min + len(col))
    if hi > lo:
        out[lo - start:hi - start] = col[lo - first_min:hi - first_min]
    return out


def shift_panel(first_min, op, cl, dmap, lo, hi):
    """Панель окна [lo, hi) суток: сутки d = сутки dmap[d] исходной панели; без источника - NaN. -> (first_min_new, op, cl)."""
    nd = hi - lo
    out = [array.array("d", [NAN]) * (nd * MIN_DAY), array.array("d", [NAN]) * (nd * MIN_DAY)]
    for d in range(lo, hi):
        s = dmap.get(d)
        if s is None:
            continue
        for c, col in enumerate((op, cl)):
            out[c][(d - lo) * MIN_DAY:(d - lo + 1) * MIN_DAY] = abs_slice(col, first_min, s * MIN_DAY, MIN_DAY)
    return lo * MIN_DAY, out[0], out[1]


# ------------------------------------------------------------------ фандинг time_ms,rate,interval_h
def shift_funding_rows(rows, dmap):
    """rows: [(time_ms, rest_of_line)] -> строки со временем, сдвинутым на (цель - источник) суток, по возрастанию времени.
    Событие суток-источника s копируется во все цели d, у которых dmap[d] == s (пятница -> пятница, суббота, воскресенье, понедельник
    в режиме trade). События вне суток-источников теряются."""
    by_src = {}
    for d, s in dmap.items():
        by_src.setdefault(s, []).append(d)
    out = []
    for t, rest in rows:
        s = (t - GRID0_MS) // (MIN_DAY * 60_000)
        for d in by_src.get(s, ()):
            out.append((t + (d - s) * MIN_DAY * 60_000, rest))
    out.sort(key=lambda r: r[0])
    return out


def shift_funding_file(src, dst, dmap):
    with gzip.open(src, "rt", encoding="utf-8", newline="") as fi:
        header = fi.readline()
        rows = []
        for line in fi:
            t, rest = line.rstrip("\r\n").split(",", 1)
            rows.append((int(t), rest))
    out = shift_funding_rows(rows, dmap)
    tmp = dst + ".part"
    with gzip.open(tmp, "wt", encoding="utf-8", newline="") as fo:
        fo.write(header)
        for t, rest in out:
            fo.write("%d,%s\n" % (t, rest))
    os.replace(tmp, dst)
    return len(out)


# ------------------------------------------------------------------ каталоги
def link_all(src_dir, dst_dir, suffix):
    """Символьные ссылки на все файлы <suffix> каталога src_dir внутри dst_dir (настоящие панели рядом со сдвинутыми)."""
    os.makedirs(dst_dir, exist_ok=True)
    n = 0
    for fn in os.listdir(src_dir):
        if fn.endswith(suffix):
            p = os.path.join(dst_dir, fn)
            if os.path.lexists(p):
                os.remove(p)
            os.symlink(os.path.realpath(os.path.join(src_dir, fn)), p)
            n += 1
    return n


def read_needs(path):
    out = []
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.reader(fh, delimiter="\t"):
            if row and row[0] != "layout":
                out.append(tuple(row[:4]))
    return out


def cmd_mkplac(a):
    lo, hi, k = a["lo"], a["hi"], a["k"]
    needs = read_needs(a["needs"])
    maps = {m: day_map(k, m, lo, hi) for m in ("cal", "trade")}
    n_p = n_f = n_miss = 0
    seen_dirs = set()
    for layout, ex, sym, mode in sorted(set(needs)):
        sp = os.path.join(a["src"], ex, sym + ".sap")
        if not os.path.exists(sp):
            n_miss += 1
            continue
        if layout == "p":
            pd, fd, name = (os.path.join(a["out"], "p", ex), os.path.join(a["out"], "f", ex), sym + TAG)
        else:
            pd, fd, name = (os.path.join(a["out"], "x", ex), os.path.join(a["out"], "fx", ex), sym)
        if (layout, ex) not in seen_dirs:
            seen_dirs.add((layout, ex))
            os.makedirs(pd, exist_ok=True)
            os.makedirs(fd, exist_ok=True)
            if layout == "p" and not a["dry"]:
                link_all(os.path.join(a["src"], ex), pd, ".sap")
                link_all(os.path.join(a["fsrc"], ex), fd, ".csv.gz")
        if a["dry"]:
            n_p += 1
            continue
        fm, op, cl = read_sap(os.path.realpath(sp))
        nfm, nop, ncl = shift_panel(fm, op, cl, maps[mode], lo, hi)
        write_sap(os.path.join(pd, name + ".sap"), nfm, nop, ncl)
        n_p += 1
        fp = os.path.join(a["fsrc"], ex, sym + ".csv.gz")
        if os.path.exists(fp):
            shift_funding_file(fp, os.path.join(fd, name + ".csv.gz"), maps[mode])
            n_f += 1
    print("mkplac: панелей %d, файлов фандинга %d, нет панели-источника %d, окно суток [%d, %d), сдвиг +%d%s" % (
        n_p, n_f, n_miss, lo, hi, k, ", dry" if a["dry"] else ""))


def selftest():
    import sel_shift as SH
    for mode in ("cal", "trade"):
        for d in range(SH.D_FORM0, SH.D_TRADE0):
            assert source_day(d, 1, mode) == SH.source_day(d, 1, mode), (mode, d)
    dm = day_map(1, "cal")
    assert dm[2466] == 2465 and 2465 not in dm and dm[2499] == 2498
    t = day_map(1, "trade")
    # 2479 = 15.10 чт; 2480 пт; 2481 сб; 2482 вс; 2483 пн -> пт 2480
    assert t[2483] == t[2482] == t[2481] == 2480 and t[2480] == 2479
    print("selftest ok")


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    if sys.argv[1] == "selftest":
        selftest()
    elif sys.argv[1] == "mkplac":
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("--needs", required=True)
        ap.add_argument("--src", default=os.path.expanduser("~/xdl9/trade"))
        ap.add_argument("--fsrc", default=os.path.expanduser("~/xdl9/funding"))
        ap.add_argument("--out", default=os.path.expanduser("~/xdl9/plac"))
        ap.add_argument("--lo", type=int, default=D_FORM0)
        ap.add_argument("--hi", type=int, default=D_END)
        ap.add_argument("--k", type=int, default=1)
        ap.add_argument("--dry", action="store_true")
        cmd_mkplac(vars(ap.parse_args(sys.argv[2:])))
    else:
        raise SystemExit(__doc__)
